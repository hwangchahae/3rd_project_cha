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
import tiktoken
import ast
import markdown
import concurrent.futures
import asyncio
import sys

# ----------------- 상수 정의 -----------------
MAIN_EXTENSIONS = ['.py', '.js', '.md']  # 분석할 주요 파일 확장자
CHUNK_SIZE = 500  # 텍스트 청크 크기
GITHUB_TOKEN = "GITHUB_TOKEN"  # 환경 변수 키 이름
KEY_FILE = ".key"  # 암호화 키 파일

# ChromaDB 기본 클라이언트 (로컬)
chroma_client = chromadb.Client()

def analyze_repository(repo_url: str, token: Optional[str] = None, session_id: Optional[str] = None) -> Dict[str, Any]:
    """
    GitHub 저장소를 분석하고 임베딩하는 메인 함수
    
    이 함수는 다음과 같은 단계로 동작합니다:
    1. GitHub 저장소를 로컬에 클론
    2. 주요 파일 목록을 가져와서 필터링 (MAIN_EXTENSIONS에 정의된 확장자만)
    3. 파일 내용을 가져와서 임베딩 처리
    4. 디렉토리 구조 트리 텍스트 생성
    
    Args:
        repo_url (str): 분석할 GitHub 저장소 URL
        token (Optional[str]): GitHub 개인 액세스 토큰
        session_id (Optional[str]): 세션 ID (기본값: owner_repo)
        
    Returns:
        Dict[str, Any]:
            'files': 분석된 파일 목록 (각 파일은 {'path': '...', 'content': '...'} 형식)
            'directory_structure': 디렉토리 구조 트리 텍스트
        
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

        # 4. 디렉토리 구조 트리 텍스트 생성
        directory_structure = fetcher.generate_directory_structure()
        
        return {
            'files': files,
            'directory_structure': directory_structure
        }
        
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

    def get_all_main_files(self, path=""):
        files = []
        dir_contents = self.get_repo_directory_contents(path)
        if isinstance(dir_contents, list):
            for item in dir_contents:
                if item['type'] == 'file' and any(item['path'].endswith(ext) for ext in MAIN_EXTENSIONS):
                    files.append(item['path'])
                elif item['type'] == 'dir':
                    files.extend(self.get_all_main_files(item['path']))
        return files

    def filter_main_files(self):
        self.files = self.get_all_main_files()
        print(f"[DEBUG] 필터링된 주요 파일: {self.files}")
        print(f"[DEBUG] 주요 파일 개수: {len(self.files)}")

    def get_file_contents(self) -> List[Dict[str, Any]]:
        """
        주요 파일의 내용을 읽어 딕셔너리 리스트로 반환
        Returns:
            List[Dict[str, Any]]: 
                파일 경로와 내용을 포함하는 딕셔너리 리스트
                [{'path': '...', 'content': '...', 'file_name': ..., 'file_type': ..., 'sha': ..., 'source_url': ...}, ...]
        """
        file_objs = []
        for path in self.files:
            doc = self.get_repo_content_as_document(path)
            if doc:
                meta = doc.metadata
                file_objs.append({
                    'path': path,
                    'content': doc.page_content,
                    'file_name': meta.get('file_name'),
                    'file_type': meta.get('file_name', '').split('.')[-1] if meta.get('file_name') else '',
                    'sha': meta.get('sha'),
                    'source_url': meta.get('source'),
                })
        return file_objs

    def generate_directory_structure(self) -> str:
        """
        저장소의 전체 디렉토리/파일 구조를 트리 형태의 텍스트로 반환
        """
        # 디렉토리 내용 재귀적으로 가져오기
        def build_tree(path=""):
            items = self.get_repo_directory_contents(path)
            tree = {}
            if not items or isinstance(items, dict) and items.get('error'):
                return tree
            for item in items:
                if item['type'] == 'file':
                    tree[f"📄 {item['name']}"] = None
                elif item['type'] == 'dir':
                    tree[f"📁 {item['name']}"] = build_tree(item['path'])
            return tree
        
        tree = build_tree()
        lines = []
        def traverse(node, prefix=""):
            for key, value in sorted(node.items()):
                lines.append(f"{prefix}{key}")
                if value is not None:
                    traverse(value, prefix + "  ")
        traverse(tree)
        return "\n".join(lines)

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

    def process_and_embed(self, files: List[Dict[str, Any]]):
        # 내부 비동기 함수 정의
        async def async_process_and_embed(files):
            import openai
            api_key = os.environ.get("OPENAI_API_KEY")
            client = openai.AsyncClient(api_key=api_key)
            enc = tiktoken.encoding_for_model("gpt-3.5-turbo")
            def safe_meta(meta):
                return {k: ('' if v is None else v if not isinstance(v, (int, float, bool)) else v) for k, v in meta.items()}
            def split_by_tokens(text, max_tokens=256, overlap=64):
                tokens = enc.encode(text)
                chunks = []
                start = 0
                while start < len(tokens):
                    end = min(start + max_tokens, len(tokens))
                    chunk = enc.decode(tokens[start:end])
                    chunks.append((chunk, start, end))
                    if end == len(tokens):
                        break
                    start += max_tokens - overlap
                return chunks
            def chunk_python_functions(source_code):
                try:
                    tree = ast.parse(source_code)
                except Exception:
                    return [(source_code, 0, len(enc.encode(source_code)), None, None, 1, len(source_code.splitlines()))]
                lines = source_code.splitlines()
                chunks = []
                for node in tree.body:
                    if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
                        start = node.lineno - 1
                        end = getattr(node, 'end_lineno', None)
                        if end is None:
                            continue
                        chunk = '\n'.join(lines[start:end])
                        name = node.name
                        class_name = node.name if isinstance(node, ast.ClassDef) else None
                        func_name = node.name if isinstance(node, ast.FunctionDef) else None
                        if len(enc.encode(chunk)) > 256:
                            for sub_chunk, t_start, t_end in split_by_tokens(chunk, max_tokens=256, overlap=64):
                                chunks.append((sub_chunk, t_start, t_end, func_name, class_name, start+1, end))
                        else:
                            chunks.append((chunk, 0, len(enc.encode(chunk)), func_name, class_name, start+1, end))
                if not chunks:
                    for chunk, t_start, t_end in split_by_tokens(source_code, max_tokens=256, overlap=64):
                        chunks.append((chunk, t_start, t_end, None, None, 1, len(source_code.splitlines())))
                return chunks
            def chunk_markdown(md_text):
                pattern = r'(\n#+ .+|\n```[\s\S]+?```|\n\s*\n)'
                parts = re.split(pattern, md_text)
                chunks = []
                for part in parts:
                    part = part.strip()
                    if not part:
                        continue
                    if len(enc.encode(part)) > 256:
                        for chunk, t_start, t_end in split_by_tokens(part, max_tokens=256, overlap=64):
                            chunks.append((chunk, t_start, t_end, None, None, None, None))
                    else:
                        chunks.append((part, 0, len(enc.encode(part)), None, None, None, None))
                return chunks
            def chunk_js(source_code):
                return [(*x, None, None, None, None) for x in split_by_tokens(source_code, max_tokens=256, overlap=64)]
            # 1. 전체 청크 수집
            all_chunks = []
            for file in files:
                content = file['content']
                path = file['path']
                ext = os.path.splitext(path)[1].lower()
                file_name = file.get('file_name')
                file_type = file.get('file_type')
                sha = file.get('sha')
                source_url = file.get('source_url')
                if ext == '.py':
                    chunks = chunk_python_functions(content)
                elif ext == '.md':
                    chunks = chunk_markdown(content)
                elif ext == '.js':
                    chunks = chunk_js(content)
                else:
                    chunks = [(*x, None, None, None, None) for x in split_by_tokens(content, max_tokens=256, overlap=64)]
                for i, (chunk, t_start, t_end, func_name, class_name, start_line, end_line) in enumerate(chunks):
                    all_chunks.append((chunk, file, i, t_start, t_end, func_name, class_name, start_line, end_line))
            # 2. 비동기 임베딩+역할태깅 함수
            async def embed_and_tag_async(args, client):
                chunk, file, i, t_start, t_end, func_name, class_name, start_line, end_line = args
                # 임베딩
                try:
                    emb_resp = await client.embeddings.create(
                        input=chunk,
                        model="text-embedding-3-small"
                    )
                    embedding = emb_resp.data[0].embedding
                except Exception as e:
                    print(f"[WARNING] 임베딩 실패: {e}")
                    embedding = [0.0] * 1536
                # 역할 태깅
                tag_prompt = f"아래 코드는 어떤 역할(기능/목적)을 하나요? 한글로 간단히 요약해줘.\n\n코드:\n{chunk}"
                try:
                    tag_resp = await client.chat.completions.create(
                        model="gpt-3.5-turbo",
                        messages=[{"role": "user", "content": tag_prompt}],
                        temperature=0.0,
                        max_tokens=32
                    )
                    role_tag = tag_resp.choices[0].message.content.strip()
                except Exception as e:
                    print(f"[WARNING] 역할 태깅 실패: {e}")
                    role_tag = ''
                return (embedding, role_tag, chunk, file, i, t_start, t_end, func_name, class_name, start_line, end_line)
            # 3. 비동기 병렬 실행 (max_concurrent=20)
            print(f"[DEBUG] 임베딩+역할태깅 asyncio 병렬 처리 시작 (청크 수: {len(all_chunks)})")
            semaphore = asyncio.Semaphore(20)
            async def sem_task(args):
                async with semaphore:
                    return await embed_and_tag_async(args, client)
            tasks = [sem_task(args) for args in all_chunks]
            results = await asyncio.gather(*tasks)
            print(f"[DEBUG] 임베딩+역할태깅 asyncio 병렬 처리 완료")
            # 4. DB 저장 (동기)
            for embedding, role_tag, chunk, file, i, t_start, t_end, func_name, class_name, start_line, end_line in results:
                file_name = file.get('file_name')
                file_type = file.get('file_type')
                sha = file.get('sha')
                source_url = file.get('source_url')
                path = file['path']
                metadata = {
                    "path": path or '',
                    "file_name": file_name or '',
                    "file_type": file_type or '',
                    "sha": sha or '',
                    "source_url": source_url or '',
                    "chunk_index": i,
                    "function_name": func_name or '',
                    "class_name": class_name or '',
                    "start_line": start_line if start_line is not None else -1,
                    "end_line": end_line if end_line is not None else -1,
                    "token_start": t_start if t_start is not None else -1,
                    "token_end": t_end if t_end is not None else -1,
                    "role_tag": role_tag
                }
                self.collection.add(
                    ids=[f"{path}_{i}"],
                    embeddings=[embedding],
                    documents=[chunk],
                    metadatas=[safe_meta(metadata)]
                )
        # 동기 함수에서 비동기 실행
        if sys.version_info >= (3, 7):
            asyncio.run(async_process_and_embed(files))
        else:
            raise RuntimeError("Python 3.7 이상에서만 지원됩니다.")
