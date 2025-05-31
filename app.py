from flask import Flask, render_template, request, redirect, url_for, jsonify
import uuid
from github_analyzer import analyze_repository
from chat_handler import handle_chat, handle_modify_request, apply_changes
from dotenv import load_dotenv
import os
import sys
import db
import traceback

load_dotenv()

key = os.environ.get("OPENAI_API_KEY")
print(f"[DEBUG] OPENAI_API_KEY loaded: {key[:8]}...{key[-4:] if key else ''}")

if not key:
    print("오류: OpenAI API 키가 설정되어 있지 않습니다. .env 파일에 OPENAI_API_KEY를 등록하세요.")
    sys.exit(1)

db.init_db()

app = Flask(__name__)

sessions = {}  # session_id: {'repo_url': ..., 'token': ..., 'files': ...}

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/chat/<session_id>')
def chat(session_id):
    return render_template('chat.html', session_id=session_id)

@app.route('/analyze', methods=['POST'])
def analyze():
    try:
        data = request.get_json()
        repo_url = data.get('repo_url')
        token = data.get('token')
        if not repo_url or not repo_url.startswith('https://github.com/'):
            return jsonify({'status': '에러', 'error': '올바른 GitHub 저장소 URL을 입력하세요.'}), 400
        session_id = str(uuid.uuid4())
        try:
            files = analyze_repository(repo_url, token, session_id)
            sessions[session_id] = {
                'repo_url': repo_url,
                'token': token,
                'files': files
            }
            return jsonify({'status': '분석 완료', 'session_id': session_id, 'files': files})
        except Exception as e:
            msg = str(e)
            print("[분석 에러]", msg)
            traceback.print_exc()
            if '404' in msg:
                return jsonify({'status': '에러', 'error': '저장소를 찾을 수 없습니다. URL과 공개/비공개 여부, 토큰을 확인하세요.'}), 400
            elif '401' in msg or '403' in msg:
                return jsonify({'status': '에러', 'error': '권한이 없습니다. 비공개 저장소는 Personal Access Token이 필요합니다.'}), 400
            elif 'OPENAI_API_KEY' in msg:
                return jsonify({'status': '에러', 'error': 'OpenAI API 키가 올바르지 않거나 누락되었습니다.'}), 400
            else:
                return jsonify({'status': '에러', 'error': f'분석 중 오류 발생: {msg}'}), 400
    except Exception as e:
        print("[분석 알 수 없는 에러]", str(e))
        traceback.print_exc()
        return jsonify({'status': '에러', 'error': f'알 수 없는 오류: {str(e)}'}), 500

@app.route('/chat', methods=['POST'])
def chat_api():
    try:
        data = request.get_json()
        session_id = data.get('session_id')
        message = data.get('message')
        if not session_id or not message:
            return jsonify({'error': '세션ID와 질문을 모두 입력하세요.'}), 400
        try:
            result = handle_chat(session_id, message)
            return jsonify(result)
        except Exception as e:
            msg = str(e)
            print("[챗봇 에러]", msg)
            traceback.print_exc()
            if 'OPENAI_API_KEY' in msg:
                return jsonify({'error': 'OpenAI API 키가 올바르지 않거나 누락되었습니다.'}), 400
            elif 'context length' in msg:
                return jsonify({'error': '질문 또는 코드가 너무 깁니다. 질문을 더 짧게 입력해 주세요.'}), 400
            else:
                return jsonify({'error': f'답변 생성 중 오류: {msg}'}), 400
    except Exception as e:
        print("[챗봇 알 수 없는 에러]", str(e))
        traceback.print_exc()
        return jsonify({'error': f'알 수 없는 오류: {str(e)}'}), 500

@app.route('/modify_request', methods=['POST'])
def modify_request():
    try:
        data = request.get_json()
        session_id = data.get('session_id')
        message = data.get('message')
        if not session_id or not message:
            return jsonify({'error': '세션ID와 수정 요청을 모두 입력하세요.'}), 400
        try:
            result = handle_modify_request(session_id, message)
            return jsonify(result)
        except Exception as e:
            msg = str(e)
            print("[코드수정 에러]", msg)
            traceback.print_exc()
            if 'OPENAI_API_KEY' in msg:
                return jsonify({'error': 'OpenAI API 키가 올바르지 않거나 누락되었습니다.'}), 400
            elif 'context length' in msg:
                return jsonify({'error': '수정 요청 또는 코드가 너무 깁니다. 요청을 더 구체적으로 입력해 주세요.'}), 400
            else:
                return jsonify({'error': f'코드 수정 중 오류: {msg}'}), 400
    except Exception as e:
        print("[코드수정 알 수 없는 에러]", str(e))
        traceback.print_exc()
        return jsonify({'error': f'알 수 없는 오류: {str(e)}'}), 500

@app.route('/apply_changes', methods=['POST'])
def apply_changes_api():
    try:
        data = request.get_json()
        session_id = data.get('session_id')
        file_name = data.get('file_name')
        new_content = data.get('new_content')
        if not session_id or not file_name or not new_content:
            return jsonify({'error': '세션ID, 파일명, 코드 내용을 모두 입력하세요.'}), 400
        try:
            result = apply_changes(session_id, file_name, new_content)
            return jsonify(result)
        except Exception as e:
            msg = str(e)
            print("[코드적용 에러]", msg)
            traceback.print_exc()
            if 'not found' in msg or 'No such file' in msg:
                return jsonify({'error': '해당 파일을 찾을 수 없습니다. 파일명을 다시 확인하세요.'}), 400
            elif 'branch' in msg:
                return jsonify({'error': '브랜치 생성 또는 커밋 중 오류가 발생했습니다.'}), 400
            else:
                return jsonify({'error': f'코드 적용 중 오류: {msg}'}), 400
    except Exception as e:
        print("[코드적용 알 수 없는 에러]", str(e))
        traceback.print_exc()
        return jsonify({'error': f'알 수 없는 오류: {str(e)}'}), 500

if __name__ == '__main__':
    app.run(debug=True) 