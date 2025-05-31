import requests
import chromadb
import os
import re
import openai
import git

# 주요 파일 확장자
MAIN_EXTENSIONS = ['.py', '.js', '.md']

# 청크 크기
CHUNK_SIZE = 500

# ChromaDB 기본 클라이언트 (로컬)
chroma_client = chromadb.Client()



def parse_github_repo(repo_url):
    """
    https://github.com/user/repo 형태에서 ('user', 'repo') 추출
    """
    m = re.match(r'https?://github.com/([^/]+)/([^/]+)', repo_url)
    if not m:
        raise ValueError('잘못된 GitHub URL')
    return m.group(1), m.group(2)

class GitHubRepositoryFetcher:
    def __init__(self, repo_url, token=None, session_id=None):
        self.repo_url = repo_url
        self.token = token
        self.headers = {'Authorization': f'token {token}'} if token else {}
        self.files = []
        self.owner, self.repo = parse_github_repo(repo_url)
        self.session_id = session_id or f"{self.owner}_{self.repo}"
        self.repo_path = f"./repos/{self.session_id}"

    def clone_repo(self):
        if not os.path.exists(self.repo_path):
            try:
                git.Repo.clone_from(self.repo_url, self.repo_path)
            except Exception as e:
                print("[DEBUG] GitHub 클론 에러:", e)
                raise

    def fetch_file_list(self):
        # GitHub API로 전체 파일 트리 가져오기
        url = f'https://api.github.com/repos/{self.owner}/{self.repo}/git/trees/HEAD?recursive=1'
        try:
            r = requests.get(url, headers=self.headers)
            if r.status_code != 200:
                print(f"[DEBUG] GitHub API 에러: {r.status_code} {r.text}")
                raise Exception(f'GitHub API 오류: {r.status_code} {r.text}')
            data = r.json()
            self.all_files = [item['path'] for item in data.get('tree', []) if item['type'] == 'blob']
        except Exception as e:
            print("[DEBUG] GitHub API 파일 목록 에러:", e)
            raise

    def fetch_file_content(self, path):
        # 파일 내용 읽기 (raw.githubusercontent.com 사용)
        url = f'https://raw.githubusercontent.com/{self.owner}/{self.repo}/HEAD/{path}'
        try:
            r = requests.get(url, headers=self.headers)
            if r.status_code == 200:
                return r.text
            print(f"[DEBUG] GitHub 파일 내용 에러: {r.status_code} {r.text}")
            return ''
        except Exception as e:
            print("[DEBUG] GitHub 파일 내용 에러:", e)
            return ''

    def filter_main_files(self):
        # 주요 파일만 선별
        self.files = [f for f in self.all_files if any(f.endswith(ext) for ext in MAIN_EXTENSIONS)]

    def get_file_contents(self):
        # 주요 파일의 내용을 읽어 [{'path': ..., 'content': ...}] 형태로 반환
        file_objs = []
        for path in self.files:
            content = self.fetch_file_content(path)
            file_objs.append({'path': path, 'content': content})
        return file_objs






class RepositoryEmbedder:
    def __init__(self, session_id):
        self.session_id = session_id
        self.collection = chroma_client.get_or_create_collection(name=f"repo_{session_id}")

    def process_and_embed(self, files):
        chunk_id = 0
        api_key = os.environ.get("OPENAI_API_KEY")
        print(f"[DEBUG] 임베딩 직전 OPENAI_API_KEY: {api_key[:8]}...{api_key[-4:]}")
        client = openai.OpenAI(api_key=api_key)
        
        for file in files:
            content = file['content']
            path = file['path']
            # 500자 단위 청크 분할
            for i in range(0, len(content), CHUNK_SIZE):
                chunk = content[i:i+CHUNK_SIZE]
                # OpenAI 임베딩 생성 (최신 방식)
                try:
                    response = client.embeddings.create(
                        input=chunk,
                        model="text-embedding-3-small"
                    )
                    embedding = response.data[0].embedding
                except Exception as e:
                    print("[DEBUG] OpenAI 임베딩 에러:", e)
                    raise
                # ChromaDB에 저장 (메타데이터: 파일경로, 청크 인덱스)
                self.collection.add(
                    ids=[f"{path}_{i//CHUNK_SIZE}"],
                    embeddings=[embedding],
                    documents=[chunk],
                    metadatas=[{"path": path, "chunk_index": i // CHUNK_SIZE}]
                )
                chunk_id += 1

def analyze_repository(repo_url, token=None, session_id=None):
    # Git 저장소에서 데이터 가져오기
    fetcher = GitHubRepositoryFetcher(repo_url, token, session_id)
    fetcher.clone_repo()
    fetcher.fetch_file_list()
    fetcher.filter_main_files()
    files = fetcher.get_file_contents()

    # 데이터 임베딩 처리
    embedder = RepositoryEmbedder(fetcher.session_id)
    embedder.process_and_embed(files)
    
    return files 