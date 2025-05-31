# git_modifier.py
import git
import os

def create_branch_and_commit(repo_path, branch_name, file_path, new_content, commit_msg):
    repo = git.Repo(repo_path)
    # 1. 새 브랜치 생성
    new_branch = repo.create_head(branch_name)
    repo.head.reference = new_branch
    repo.head.reset(index=True, working_tree=True)
    # 2. 파일 수정
    abs_path = os.path.join(repo_path, file_path)
    with open(abs_path, 'w', encoding='utf-8') as f:
        f.write(new_content)
    repo.index.add([file_path])
    # 3. 커밋
    repo.index.commit(commit_msg)
    # 4. 푸시 (토큰 인증 필요, 미구현)
    # origin = repo.remote(name='origin')
    # origin.push(new_branch)
    return True 