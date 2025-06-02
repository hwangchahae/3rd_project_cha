import os
import subprocess
from typing import Dict, Optional, Any
from dotenv import load_dotenv

class CodeModifier:
    def __init__(self):
        load_dotenv()
        self.token = os.getenv('GITHUB_TOKEN')

    def get_github_token(self) -> Optional[str]:
        """
        GitHub 토큰을 가져오거나 사용자에게 입력받는 함수
        
        Returns:
            Optional[str]: GitHub 토큰
        """
        if not self.token:
            print("\nGitHub 토큰이 필요합니다.")
            print("GitHub 토큰은 https://github.com/settings/tokens 에서 생성할 수 있습니다.")
            self.token = input("GitHub 토큰을 입력하세요: ").strip()
            
            # 토큰 저장 여부 확인
            save_token = input("이 토큰을 저장하시겠습니까? (y/n): ").strip().lower()
            if save_token == 'y':
                # .env 파일에 토큰 저장
                with open('.env', 'a', encoding='utf-8') as f:
                    f.write(f"\nGITHUB_TOKEN={self.token}")
                print("토큰이 .env 파일에 저장되었습니다.")
        
        return self.token

    def create_new_file(self, repo_path: str, file_path: str, content: str) -> Dict[str, Any]:
        """
        새로운 파일을 생성하는 함수
        
        Args:
            repo_path (str): 레포지토리 경로
            file_path (str): 생성할 파일 경로
            content (str): 파일 내용
            
        Returns:
            Dict[str, Any]: 파일 생성 결과
        """
        try:
            # GitHub 토큰 확인
            token = self.get_github_token()
            if not token:
                return {
                    'success': False,
                    'error': "GitHub 토큰이 필요합니다."
                }

            # 전체 경로 생성
            full_path = os.path.join(repo_path, file_path)
            
            # 디렉토리가 없으면 생성
            os.makedirs(os.path.dirname(full_path), exist_ok=True)
            
            # 파일이 이미 존재하는지 확인
            if os.path.exists(full_path):
                return {
                    'success': False,
                    'error': f"파일이 이미 존재합니다: {file_path}"
                }
            
            # 새 파일 생성
            with open(full_path, 'w', encoding='utf-8') as f:
                f.write(content)
            
            return {
                'success': True,
                'message': f'새로운 파일이 생성되었습니다: {file_path}'
            }
            
        except Exception as e:
            print(f"파일 생성 중 오류 발생: {e}")
            return {
                'success': False,
                'error': f"파일 생성 중 오류 발생: {str(e)}"
            }

    def commit_changes(self, repo_path: str, file_path: str, content: str, commit_message: str) -> Dict[str, Any]:
        """
        코드 변경사항을 커밋하는 함수
        
        Args:
            repo_path (str): 레포지토리 경로
            file_path (str): 수정할 파일 경로
            content (str): 새로운 코드 내용
            commit_message (str): 커밋 메시지
            
        Returns:
            Dict[str, Any]: 커밋 결과
        """
        try:
            # GitHub 토큰 확인
            token = self.get_github_token()
            if not token:
                return {
                    'success': False,
                    'error': "GitHub 토큰이 필요합니다."
                }

            # 현재 디렉토리 저장
            current_dir = os.getcwd()
            
            # 레포지토리 디렉토리로 이동
            os.chdir(repo_path)
            
            # 파일 내용 수정
            full_path = os.path.join(repo_path, file_path)
            os.makedirs(os.path.dirname(full_path), exist_ok=True)
            with open(full_path, 'w', encoding='utf-8') as f:
                f.write(content)
            
            # 변경사항 스테이징
            subprocess.run(['git', 'add', file_path], check=True)
            
            # 커밋
            subprocess.run(['git', 'commit', '-m', commit_message], check=True)
            
            # 원래 디렉토리로 복귀
            os.chdir(current_dir)
            
            return {
                'success': True,
                'message': '코드 변경사항이 성공적으로 커밋되었습니다.'
            }
            
        except subprocess.CalledProcessError as e:
            print(f"Git 커밋 중 오류 발생: {e}")
            return {
                'success': False,
                'error': f"Git 커밋 중 오류 발생: {str(e)}"
            }
        except Exception as e:
            print(f"코드 변경사항 적용 중 오류 발생: {e}")
            return {
                'success': False,
                'error': f"코드 변경사항 적용 중 오류 발생: {str(e)}"
            }

    def push_changes(self, repo_path: str) -> Dict[str, Any]:
        """
        커밋된 변경사항을 원격 저장소에 푸시하는 함수
        
        Args:
            repo_path (str): 레포지토리 경로
            
        Returns:
            Dict[str, Any]: 푸시 결과
        """
        try:
            # GitHub 토큰 확인
            token = self.get_github_token()
            if not token:
                return {
                    'success': False,
                    'error': "GitHub 토큰이 필요합니다."
                }

            # 현재 디렉토리 저장
            current_dir = os.getcwd()
            
            # 레포지토리 디렉토리로 이동
            os.chdir(repo_path)
            
            # 푸시
            subprocess.run(['git', 'push'], check=True)
            
            # 원래 디렉토리로 복귀
            os.chdir(current_dir)
            
            return {
                'success': True,
                'message': '변경사항이 성공적으로 푸시되었습니다.'
            }
            
        except subprocess.CalledProcessError as e:
            print(f"Git 푸시 중 오류 발생: {e}")
            return {
                'success': False,
                'error': f"Git 푸시 중 오류 발생: {str(e)}"
            }
        except Exception as e:
            print(f"푸시 중 오류 발생: {e}")
            return {
                'success': False,
                'error': f"푸시 중 오류 발생: {str(e)}"
            }

    def create_branch(self, repo_path: str, branch_name: str) -> Dict[str, Any]:
        """
        새로운 브랜치를 생성하는 함수
        
        Args:
            repo_path (str): 레포지토리 경로
            branch_name (str): 생성할 브랜치 이름
            
        Returns:
            Dict[str, Any]: 브랜치 생성 결과
        """
        try:
            # GitHub 토큰 확인
            token = self.get_github_token()
            if not token:
                return {
                    'success': False,
                    'error': "GitHub 토큰이 필요합니다."
                }

            # 현재 디렉토리 저장
            current_dir = os.getcwd()
            
            # 레포지토리 디렉토리로 이동
            os.chdir(repo_path)
            
            # 새 브랜치 생성
            subprocess.run(['git', 'checkout', '-b', branch_name], check=True)
            
            # 원래 디렉토리로 복귀
            os.chdir(current_dir)
            
            return {
                'success': True,
                'message': f'새로운 브랜치가 생성되었습니다: {branch_name}'
            }
            
        except subprocess.CalledProcessError as e:
            print(f"브랜치 생성 중 오류 발생: {e}")
            return {
                'success': False,
                'error': f"브랜치 생성 중 오류 발생: {str(e)}"
            }
        except Exception as e:
            print(f"브랜치 생성 중 오류 발생: {e}")
            return {
                'success': False,
                'error': f"브랜치 생성 중 오류 발생: {str(e)}"
            }

def main():
    # 사용 예시
    modifier = CodeModifier()
    repo_path = "./repos/example_repo"
    branch_name = "feature/new-branch"
    
    # 1. 새 브랜치 생성
    branch_result = modifier.create_branch(repo_path, branch_name)
    if branch_result['success']:
        print(branch_result['message'])
        
        file_path = "new_file.py"
        content = "print('Hello, World!')"
        commit_message = "새로운 파일 추가"
        
        # 2. 새 파일 생성
        create_result = modifier.create_new_file(repo_path, file_path, content)
        if create_result['success']:
            print(create_result['message'])
            
            # 3. 커밋
            commit_result = modifier.commit_changes(repo_path, file_path, content, commit_message)
            if commit_result['success']:
                print(commit_result['message'])
                
                # 4. 푸시
                push_result = modifier.push_changes(repo_path)
                if push_result['success']:
                    print(push_result['message'])
                else:
                    print(f"푸시 오류: {push_result['error']}")
            else:
                print(f"커밋 오류: {commit_result['error']}")
        else:
            print(f"파일 생성 오류: {create_result['error']}")
    else:
        print(f"브랜치 생성 오류: {branch_result['error']}")

if __name__ == "__main__":
    main() 
