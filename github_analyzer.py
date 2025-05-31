"""
GitHub 저장소 분석 및 임베딩을 위한 모듈

이 모듈은 GitHub 저장소의 내용을 가져와서 분석하고, 
LangChain Document로 변환한 후 ChromaDB에 임베딩하여 저장하는 기능을 제공합니다.

주요 클래스:
    - GitHubRepositoryFetcher: GitHub 저장소에서 파일을 가져오는 클래스
    - RepositoryEmbedder: 저장소 내용을 임베딩하는 클래스

주요 함수:
    - analyze_repository: GitHub 저장소를 분석하고 임베딩하는 메인 함수
"""

import requests
import chromadb
import os
import re
import openai
import git
import base64
from typing import Optional, List, Dict, Any, Tuple
from langchain.schema import Document
from cryptography.fernet import Fernet

# ----------------- 상수 정의 -----------------
MAIN_EXTENSIONS = ['.py', '.js', '.md']  # 분석할 주요 파일 확장자
CHUNK_SIZE = 500  # 텍스트 청크 크기
GITHUB_TOKEN = "GITHUB_TOKEN"  # 환경 변수 키 이름
KEY_FILE = ".key"  # 암호화 키 파일

# ChromaDB 기본 클라이언트 (로컬)
chroma_client = chromadb.Client()

def analyze_repository(repo_url: str, token: Optional[str] = None, session_id: Optional[str] = None) -> List[Dict[str, str]]:
    """
    GitHub 저장소를 분석하고 임베딩하는 메인 함수
    
    이 함수는 다음과 같은 단계로 동작합니다:
    1. GitHub 저장소를 로컬에 클론
    2. 주요 파일 목록을 가져와서 필터링 (MAIN_EXTENSIONS에 정의된 확장자만)
    3. 파일 내용을 가져와서 임베딩 처리
    
    Args:
        repo_url (str): 분석할 GitHub 저장소 URL
        token (Optional[str]): GitHub 개인 액세스 토큰
        session_id (Optional[str]): 세션 ID (기본값: owner_repo)
        
    Returns:
        List[Dict[str, str]]: 
            분석된 파일 목록
            각 파일은 {'path': '...', 'content': '...'} 형식
            
    Raises:
        ValueError: 잘못된 GitHub URL인 경우
        Exception: 저장소 클론 실패 시
    """
    try:
        # 1. Git 저장소에서 데이터 가져오기
        fetcher = GitHubRepositoryFetcher(repo_url, token, session_id)
        fetcher.clone_repo()
        
        # 2. 주요 파일 필터링 및 내용 가져오기
        fetcher.filter_main_files()  # MAIN_EXTENSIONS에 정의된 확장자만 필터링
        files = fetcher.get_file_contents()

        # 3. 데이터 임베딩 처리
        embedder = RepositoryEmbedder(fetcher.session_id)
        embedder.process_and_embed(files)
        
        return files
        
    except ValueError as e:
        print(f"[오류] 잘못된 GitHub URL: {e}")
        raise
    except Exception as e:
        print(f"[오류] 저장소 분석 실패: {e}")
        raise

class GitHubRepositoryFetcher:
    """
    GitHub 저장소에서 파일을 가져오는 클래스
    
    이 클래스는 GitHub API를 사용하여 저장소의 파일과 디렉토리를 가져오고,
    LangChain Document 형식으로 변환하는 기능을 제공합니다.
    """
    
    def __init__(self, repo_url: str, token: Optional[str] = None, session_id: Optional[str] = None):
        """
        GitHub 저장소 뷰어 초기화
        
        Args:
            repo_url (str): GitHub 저장소 URL
            token (Optional[str]): GitHub 개인 액세스 토큰
            session_id (Optional[str]): 세션 ID (기본값: owner_repo)
        """
        self.repo_url = repo_url
        self.token = token
        self.headers = {'Authorization': f'token {token}'} if token else {}
        self.files = []
        
        # 저장소 정보 추출
        self.owner, self.repo, self.path = self.extract_repo_info(repo_url)
        if not self.owner or not self.repo:
            raise ValueError("Invalid GitHub repository URL")
            
        # 세션 및 저장소 경로 설정
        self.session_id = session_id or f"{self.owner}_{self.repo}"
        self.repo_path = f"./repos/{self.session_id}"
        
        # ChromaDB 컬렉션 초기화
        self.collection = chroma_client.get_or_create_collection(
            name=self.session_id,
            metadata={"description": f"Repository: {self.owner}/{self.repo}"}
        )

    def create_error_response(self, message: str, status_code: int) -> Dict[str, Any]:
        """
        API 에러 응답 생성
        
        Args:
            message (str): 에러 메시지
            status_code (int): HTTP 상태 코드
            
        Returns:
            Dict[str, Any]: 에러 정보를 포함하는 딕셔너리
        """
        return {
            'error': True,
            'message': message,
            'status_code': status_code
        }

    def handle_github_response(self, response: requests.Response, path: str = None) -> Dict[str, Any]:
        """
        GitHub API 응답 처리
        
        Args:
            response (requests.Response): GitHub API 응답
            path (str, optional): 요청한 파일/디렉토리 경로
            
        Returns:
            Dict[str, Any]: 처리된 응답 데이터 또는 에러 정보
        """
        if response.status_code == 403:
            return self.create_error_response(
                'GitHub API 호출 제한에 도달했습니다. 잠시 후 다시 시도해주세요.',
                403
            )
            
        if response.status_code == 404:
            return self.create_error_response(
                f'파일을 찾을 수 없습니다: {path}' if path else '요청한 리소스를 찾을 수 없습니다.',
                404
            )
            
        if response.status_code == 401:
            return self.create_error_response(
                '비공개 저장소에 접근하려면 GitHub 토큰이 필요합니다.',
                401
            )
            
        if response.status_code != 200:
            return self.create_error_response(
                f'GitHub API 오류: {response.text}',
                response.status_code
            )
        
        return response.json()

    def extract_repo_info(self, url: str) -> Tuple[Optional[str], Optional[str], Optional[str]]:
        """
        GitHub URL에서 소유자, 저장소 이름, 파일 경로를 추출
        
        Args:
            url (str): GitHub 저장소 URL
            
        Returns:
            Tuple[Optional[str], Optional[str], Optional[str]]: 
                (owner, repo, path) 또는 (None, None, None)
        """
        try:
            # URL 정규화
            url = url.strip().rstrip('/')
            if url.endswith('.git'):
                url = url[:-4]
                
            # URL 파싱
            parts = url.split('/')
            if 'github.com' in parts:
                github_index = parts.index('github.com')
                if len(parts) >= github_index + 3:
                    owner = parts[github_index + 1]
                    repo = parts[github_index + 2]
                    path = '/'.join(parts[github_index + 3:]) if len(parts) > github_index + 3 else None
                    return owner, repo, path
        except Exception as e:
            print(f"URL 파싱 중 오류 발생: {e}")
        return None, None, None

    def clone_repo(self):
        """
        GitHub 저장소를 로컬에 클론
        
        Raises:
            Exception: 클론 실패 시 예외 발생
        """
        if not os.path.exists(self.repo_path):
            try:
                git.Repo.clone_from(self.repo_url, self.repo_path)
            except Exception as e:
                print("[DEBUG] GitHub 클론 에러:", e)
                raise

    def get_repo_directory_contents(self, path: str = "") -> Optional[List[Dict[str, Any]]]:
        """
        GitHub API를 사용하여 저장소의 디렉토리 내용을 가져옴
        
        Args:
            path (str): 디렉토리 경로 (기본값: 루트 디렉토리)
            
        Returns:
            Optional[List[Dict[str, Any]]]: 
                디렉토리 내용 목록 또는 에러 정보
                각 항목은 GitHub API 응답 형식의 파일/디렉토리 정보
        """
        try:
            # API 호출 준비
            url = f"https://api.github.com/repos/{self.owner}/{self.repo}/contents/{path}"
            headers = {
                "Accept": "application/vnd.github.v3+json"
            }
            if self.token:
                headers["Authorization"] = f"token {self.token}"
            
            # API 요청 실행
            response = requests.get(url, headers=headers)
            content = self.handle_github_response(response, path)
            
            # 응답 검증
            if isinstance(content, dict) and content.get('error'):
                return content
            if isinstance(content, list):
                return content
            return self.create_error_response("잘못된 응답 형식", 500)
            
        except requests.exceptions.RequestException as e:
            return self.create_error_response(f'API 요청 실패: {str(e)}', 500)
        except Exception as e:
            return self.create_error_response(f'예상치 못한 오류: {str(e)}', 500)

    def get_repo_content_as_document(self, path: str) -> Optional[Document]:
        """
        GitHub API를 사용하여 저장소의 파일 내용을 LangChain Document로 가져옴
        
        Args:
            path (str): 파일 경로
            
        Returns:
            Optional[Document]: 
                LangChain Document 객체 또는 None (파일이 없는 경우)
                Document는 파일 내용과 메타데이터를 포함
        """
        try:
            # API 호출 준비
            url = f"https://api.github.com/repos/{self.owner}/{self.repo}/contents/{path}"
            headers = {
                "Accept": "application/vnd.github.v3+json"
            }
            if self.token:
                headers["Authorization"] = f"token {self.token}"
            
            # API 요청 실행
            response = requests.get(url, headers=headers)
            content_data = self.handle_github_response(response, path)
            
            # 에러 체크
            if not content_data or isinstance(content_data, dict) and content_data.get('error'):
                return None
            
            # Base64 디코딩
            content = base64.b64decode(content_data['content']).decode('utf-8')
            
            # Document 객체 생성
            return Document(
                page_content=content,
                metadata={
                    'source': content_data['html_url'],
                    'file_name': content_data['name'],
                    'file_path': content_data['path'],
                    'sha': content_data['sha'],
                    'size': content_data['size'],
                    'type': content_data['type']
                }
            )
        except Exception as e:
            print(f"Document 변환 중 오류 발생: {e}")
            return None

    def get_repo_directory_as_documents(self, path: str = "") -> List[Document]:
        """
        GitHub API를 사용하여 저장소의 디렉토리 내용을 LangChain Document 리스트로 가져옴
        
        Args:
            path (str): 디렉토리 경로 (기본값: 루트 디렉토리)
            
        Returns:
            List[Document]: 
                LangChain Document 객체 리스트
                각 Document는 파일의 내용과 메타데이터를 포함
        """
        documents = []
        try:
            # 디렉토리 내용 가져오기
            dir_contents = self.get_repo_directory_contents(path)
            if not dir_contents:
                return documents
                
            # 각 항목 처리
            for item in dir_contents:
                if item['type'] == 'file':
                    # 파일인 경우 Document로 변환
                    doc = self.get_repo_content_as_document(item['path'])
                    if doc:
                        documents.append(doc)
                elif item['type'] == 'dir':
                    # 디렉토리인 경우 재귀적으로 처리
                    sub_docs = self.get_repo_directory_as_documents(item['path'])
                    documents.extend(sub_docs)
                    
            return documents
        except Exception as e:
            print(f"[API] Document 리스트 생성 실패: {str(e)}")
            return documents

    def get_all_repo_contents(self) -> List[Document]:
        """
        GitHub 저장소의 모든 파일과 폴더를 LangChain Document 리스트로 가져옴
        
        Returns:
            List[Document]: 모든 파일의 LangChain Document 객체 리스트
        """
        return self.get_repo_directory_as_documents()

    def filter_main_files(self):
        """
        주요 파일만 선별
        
        MAIN_EXTENSIONS에 정의된 확장자를 가진 파일만 self.files에 저장
        """
        dir_contents = self.get_repo_directory_contents()
        if isinstance(dir_contents, list):
            self.files = [item['path'] for item in dir_contents 
                         if item['type'] == 'file' and 
                         any(item['path'].endswith(ext) for ext in MAIN_EXTENSIONS)]

    def get_file_contents(self) -> List[Dict[str, str]]:
        """
        주요 파일의 내용을 읽어 딕셔너리 리스트로 반환
        
        Returns:
            List[Dict[str, str]]: 
                파일 경로와 내용을 포함하는 딕셔너리 리스트
                [{'path': '...', 'content': '...'}, ...]
        """
        file_objs = []
        for path in self.files:
            doc = self.get_repo_content_as_document(path)
            if doc:
                file_objs.append({
                    'path': path,
                    'content': doc.page_content
                })
        return file_objs

    # ----------------- 토큰 관련 기능 -----------------
    @staticmethod
    def generate_key() -> bytes:
        """
        암호화 키 생성
        
        Returns:
            bytes: 생성된 암호화 키
        """
        if not os.path.exists(KEY_FILE):
            key = Fernet.generate_key()
            with open(KEY_FILE, 'wb') as key_file:
                key_file.write(key)
            return key
        else:
            with open(KEY_FILE, 'rb') as key_file:
                return key_file.read()

    @staticmethod
    def encrypt_token(token: str) -> str:
        """
        토큰 암호화
        
        Args:
            token (str): 암호화할 토큰
            
        Returns:
            str: 암호화된 토큰
        """
        key = GitHubRepositoryFetcher.generate_key()
        f = Fernet(key)
        return f.encrypt(token.encode()).decode()

    @staticmethod
    def decrypt_token(encrypted_token: str) -> str:
        """
        토큰 복호화
        
        Args:
            encrypted_token (str): 복호화할 토큰
            
        Returns:
            str: 복호화된 토큰
        """
        key = GitHubRepositoryFetcher.generate_key()
        f = Fernet(key)
        return f.decrypt(encrypted_token.encode()).decode()

    @staticmethod
    def update_token(token: str) -> bool:
        """
        환경 변수 파일에 GitHub 토큰 업데이트
        
        Args:
            token (str): 업데이트할 토큰
            
        Returns:
            bool: 업데이트 성공 여부
        """
        try:
            # 토큰 암호화
            encrypted_token = GitHubRepositoryFetcher.encrypt_token(token)
            
            # 기존 내용 읽기
            with open(".env", 'r', encoding='utf-8') as f:
                lines = f.readlines()
            
            # GitHub 토큰 찾아서 교체
            token_found = False
            for i, line in enumerate(lines):
                if line.startswith(f"{GITHUB_TOKEN}="):
                    lines[i] = f"{GITHUB_TOKEN}={encrypted_token}\n"
                    token_found = True
                    break
            
            # 토큰이 없으면 새로 추가
            if not token_found:
                lines.append(f"{GITHUB_TOKEN}={encrypted_token}\n")
            
            # 파일 다시 쓰기
            with open(".env", 'w', encoding='utf-8') as f:
                f.writelines(lines)
            
            return True
        except Exception as e:
            print(f"[오류] 토큰 저장 실패: {str(e)}")
            return False


class RepositoryEmbedder:
    """
    저장소 내용을 임베딩하는 클래스
    
    이 클래스는 GitHub 저장소의 파일 내용을 청크로 나누고,
    OpenAI API를 사용하여 임베딩한 후 ChromaDB에 저장합니다.
    """
    
    def __init__(self, session_id: str):
        """
        임베더 초기화
        
        Args:
            session_id (str): 세션 ID
        """
        self.session_id = session_id
        self.collection = chroma_client.get_or_create_collection(name=f"repo_{session_id}")

    def process_and_embed(self, files: List[Dict[str, str]]):
        """
        파일 내용을 처리하고 임베딩
        
        Args:
            files (List[Dict[str, str]]): 
                처리할 파일 목록
                각 파일은 {'path': '...', 'content': '...'} 형식
        """
        chunk_id = 0
        api_key = os.environ.get("OPENAI_API_KEY")
        print(f"[DEBUG] 임베딩 직전 OPENAI_API_KEY: {api_key[:8]}...{api_key[-4:]}")
        client = openai.OpenAI(api_key=api_key)
        
        for file in files:
            content = file['content']
            path = file['path']
            
            # 500자 단위로 청크 분할
            for i in range(0, len(content), CHUNK_SIZE):
                chunk = content[i:i+CHUNK_SIZE]
                
                # OpenAI 임베딩 생성
                try:
                    response = client.embeddings.create(
                        input=chunk,
                        model="text-embedding-3-small"
                    )
                    embedding = response.data[0].embedding
                except Exception as e:
                    print("[DEBUG] OpenAI 임베딩 에러:", e)
                    raise
                    
                # ChromaDB에 저장
                self.collection.add(
                    ids=[f"{path}_{i//CHUNK_SIZE}"],
                    embeddings=[embedding],
                    documents=[chunk],
                    metadatas=[{"path": path, "chunk_index": i // CHUNK_SIZE}]
                )
                chunk_id += 1

