from flask import Flask, render_template, request, redirect, url_for, jsonify, Response
import uuid
from github_analyzer import analyze_repository
from chat_handler import handle_chat, handle_modify_request, apply_changes
from dotenv import load_dotenv
import os
import sys
import db
import traceback
import json
import openai
from code_modifier import CodeModifier

load_dotenv()

import openai
openai.api_key = os.environ.get("OPENAI_API_KEY")

key = os.environ.get("OPENAI_API_KEY")
print(f"[DEBUG] OPENAI_API_KEY loaded: {key[:8]}...{key[-4:] if key else ''}")

if not key:
    print("오류: OpenAI API 키가 설정되어 있지 않습니다. .env 파일에 OPENAI_API_KEY를 등록하세요.")
    sys.exit(1)

db.init_db()

# 세션 데이터를 파일에 저장하고 로드하는 함수
def save_sessions(sessions_data):
    try:
        os.makedirs('sessions', exist_ok=True)
        with open('sessions/sessions.json', 'w', encoding='utf-8') as f:
            json.dump(sessions_data, f, ensure_ascii=False, indent=2)
        print(f"[DEBUG] 세션 데이터 저장 완료 (세션 수: {len(sessions_data)})")
    except Exception as e:
        print(f"[DEBUG] 세션 데이터 저장 오류: {e}")

def load_sessions():
    try:
        if os.path.exists('sessions/sessions.json'):
            with open('sessions/sessions.json', 'r', encoding='utf-8') as f:
                sessions_data = json.load(f)
            print(f"[DEBUG] 세션 데이터 로드 완료 (세션 수: {len(sessions_data)})")
            return sessions_data
    except Exception as e:
        print(f"[DEBUG] 세션 데이터 로드 오류: {e}")
    return {}

app = Flask(__name__)

sessions = load_sessions()  # session_id: {'repo_url': ..., 'token': ..., 'files': ...}

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
        
        # 새 세션 ID 생성
        session_id = str(uuid.uuid4())
        
        # 분석 진행 상황을 위한 응답 헤더 설정
        def generate_progress():
            yield json.dumps({'status': '분석 시작', 'progress': 0}) + '\n'
            
            try:
                # 저장소 분석 시작
                yield json.dumps({'status': '저장소 클론 중...', 'progress': 10}) + '\n'
                
                print(f"[DEBUG] analyze_repository 호출 시작 (repo_url: {repo_url}, session_id: {session_id})")
                try:
                    result = analyze_repository(repo_url, token, session_id)
                    print(f"[DEBUG] analyze_repository 결과: {list(result.keys())}")
                    
                    if 'files' not in result or 'directory_structure' not in result:
                        print(f"[ERROR] analyze_repository 결과가 올바르지 않습니다: {result}")
                        raise Exception("analyze_repository가 올바른 결과를 반환하지 않았습니다.")
                    
                    files = result['files']
                    directory_structure = result['directory_structure']
                    
                    print(f"[DEBUG] 분석된 파일 수: {len(files)}")
                    print(f"[DEBUG] 디렉토리 구조 길이: {len(directory_structure) if directory_structure else 0}")
                    
                    yield json.dumps({'status': '파일 분석 완료', 'progress': 60}) + '\n'
                except Exception as e:
                    print(f"[ERROR] analyze_repository 호출 중 오류: {e}")
                    traceback.print_exc()
                    raise e
                
                # 디렉토리 구조 정보 로그 추가
                if directory_structure:
                    print(f"[DEBUG] 디렉토리 구조 정보 생성 성공 (길이: {len(directory_structure)} 문자)")
                    # 전체 디렉토리 구조 출력
                    print("[DEBUG] 디렉토리 구조 전체:\n" + directory_structure)
                    yield json.dumps({'status': '디렉토리 구조 생성 완료', 'progress': 80}) + '\n'
                else:
                    print("[DEBUG] 디렉토리 구조 정보가 생성되지 않았습니다.")
                    yield json.dumps({'status': '디렉토리 구조 생성 실패', 'progress': 80}) + '\n'
                
                # 기존 세션들 비활성화
                for sid in sessions:
                    sessions[sid]['is_active'] = False
                
                # 새 세션 데이터 저장 및 활성화
                sessions[session_id] = {
                    'repo_url': repo_url,
                    'token': token,
                    'files': files,
                    'directory_structure': directory_structure,
                    'is_active': True  # 새 세션 활성화
                }
                
                # 세션 데이터를 파일에 저장
                save_sessions(sessions)
                
                yield json.dumps({'status': '세션 데이터 저장 완료', 'progress': 90}) + '\n'
                yield json.dumps({
                    'status': '분석 완료', 
                    'progress': 100,
                    'session_id': session_id, 
                    'file_count': len(files)
                }) + '\n'
                
            except Exception as e:
                error_msg = str(e)
                print(f"[ERROR] 저장소 분석 중 오류 발생: {error_msg}")
                yield json.dumps({'status': '에러', 'error': error_msg, 'progress': -1}) + '\n'
        
        return Response(generate_progress(), mimetype='application/x-ndjson')
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

@app.route('/create_branch', methods=['POST'])
def create_branch():
    try:
        data = request.get_json()
        branch_name = data.get('branch_name')
        
        if not branch_name:
            return jsonify({'error': '브랜치 이름을 입력하세요.'}), 400
            
        # 현재 활성화된 세션 찾기
        active_session_id = None
        for session_id, session_data in sessions.items():
            if session_data.get('is_active', False):
                active_session_id = session_id
                break
                
        if not active_session_id:
            return jsonify({'error': '활성화된 세션이 없습니다. 먼저 저장소를 분석해주세요.'}), 400
            
        repo_path = f"./repos/{active_session_id}"
        
        modifier = CodeModifier()
        result = modifier.create_branch(repo_path, branch_name)
        
        if result['success']:
            return jsonify({
                'success': True,
                'message': result['message']
            })
        else:
            return jsonify({
                'success': False,
                'error': result['error']
            }), 400
            
    except Exception as e:
        print("[브랜치 생성 에러]", str(e))
        traceback.print_exc()
        return jsonify({'error': f'알 수 없는 오류: {str(e)}'}), 500

@app.route('/create_file', methods=['POST'])
def create_file():
    try:
        data = request.get_json()
        file_path = data.get('file_path')
        content = data.get('content')
        
        if not all([file_path, content]):
            return jsonify({'error': '파일 경로와 내용을 모두 입력하세요.'}), 400
            
        # 현재 활성화된 세션 찾기
        active_session_id = None
        for session_id, session_data in sessions.items():
            if session_data.get('is_active', False):
                active_session_id = session_id
                break
                
        if not active_session_id:
            return jsonify({'error': '활성화된 세션이 없습니다. 먼저 저장소를 분석해주세요.'}), 400
            
        repo_path = f"./repos/{active_session_id}"
        
        modifier = CodeModifier()
        result = modifier.create_new_file(repo_path, file_path, content)
        
        if result['success']:
            return jsonify({
                'success': True,
                'message': result['message']
            })
        else:
            return jsonify({
                'success': False,
                'error': result['error']
            }), 400
            
    except Exception as e:
        print("[파일 생성 에러]", str(e))
        traceback.print_exc()
        return jsonify({'error': f'알 수 없는 오류: {str(e)}'}), 500

@app.route('/commit_changes', methods=['POST'])
def commit_changes_route():
    try:
        data = request.get_json()
        file_path = data.get('file_path')
        content = data.get('content')
        commit_message = data.get('commit_message')
        
        if not all([file_path, content, commit_message]):
            return jsonify({'error': '파일 경로, 내용, 커밋 메시지를 모두 입력하세요.'}), 400
            
        # 현재 활성화된 세션 찾기
        active_session_id = None
        for session_id, session_data in sessions.items():
            if session_data.get('is_active', False):
                active_session_id = session_id
                break
                
        if not active_session_id:
            return jsonify({'error': '활성화된 세션이 없습니다. 먼저 저장소를 분석해주세요.'}), 400
            
        repo_path = f"./repos/{active_session_id}"
        
        modifier = CodeModifier()
        result = modifier.commit_changes(repo_path, file_path, content, commit_message)
        
        if result['success']:
            return jsonify({
                'success': True,
                'message': result['message']
            })
        else:
            return jsonify({
                'success': False,
                'error': result['error']
            }), 400
            
    except Exception as e:
        print("[커밋 에러]", str(e))
        traceback.print_exc()
        return jsonify({'error': f'알 수 없는 오류: {str(e)}'}), 500

@app.route('/push_changes', methods=['POST'])
def push_changes():
    try:
        # 현재 활성화된 세션 찾기
        active_session_id = None
        for session_id, session_data in sessions.items():
            if session_data.get('is_active', False):
                active_session_id = session_id
                break
                
        if not active_session_id:
            return jsonify({'error': '활성화된 세션이 없습니다. 먼저 저장소를 분석해주세요.'}), 400
            
        repo_path = f"./repos/{active_session_id}"
        
        modifier = CodeModifier()
        result = modifier.push_changes(repo_path)
        
        if result['success']:
            return jsonify({
                'success': True,
                'message': result['message']
            })
        else:
            return jsonify({
                'success': False,
                'error': result['error']
            })
            
    except Exception as e:
        print("[푸시 에러]", str(e))
        traceback.print_exc()
        return jsonify({'error': f'알 수 없는 오류: {str(e)}'}), 500

if __name__ == '__main__':
    app.run(debug=True) 
