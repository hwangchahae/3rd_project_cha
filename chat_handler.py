# chat_handler.py

import openai
import chromadb
from github_analyzer import chroma_client
from git_modifier import create_branch_and_commit
import re

# top-k 유사 청크 개수
TOP_K = 5

# 더 구체적이고 엄격한 시스템 프롬프트
SYSTEM_PROMPT_QA = (
    "당신은 친절하고 전문적인 소프트웨어 엔지니어 AI입니다. "
    "아래의 코드 컨텍스트는 [파일명/함수명/클래스명/라인/sha/역할] 등 메타데이터와 함께 제공됩니다. "
    "질문에 대해 반드시 코드의 역할, 위치(파일명, 함수명, 라인 등), 역할 태그를 근거로 명확하게 설명하세요. "
    "답변에는 반드시 근거(파일명, 함수명, 클래스명, 라인, 역할 태그 등)를 명시하세요. "
    "프로젝트 디렉토리 구조와 코드 컨텍스트를 적극적으로 활용하여, 전체 구조와 맥락을 바탕으로 답변하세요. "
    "코드 설명, 예시, 한글 주석, 단계별 설명, 표, 요약 등 다양한 방식으로 친절하게 설명하세요. "
    "모르는 부분이나 코드에 없는 정보는 추측하지 말고, '해당 정보는 코드에 없습니다'라고 명확히 밝혀주세요. "
    "질문이 역할 기반일 경우, 역할 태그와 가장 관련 있는 코드 근거를 들어 설명하세요. "
    "답변의 신뢰도를 높이기 위해, 항상 답변의 출처(파일명, 함수명, 역할 등)를 함께 제시하세요. "
)
SYSTEM_PROMPT_MODIFY = (
    "당신은 코드 리팩터링 및 버그 수정에 특화된 AI입니다. "
    "사용자의 요청을 코드 컨텍스트와 함께 분석하여, 전체 코드를 수정해줍니다. "
    "수정된 코드는 반드시 // FILE: 파일명\n<전체 코드> 형식으로, 주석과 함께, 한글로 친절하게 작성하세요. "
    "불필요한 변경은 하지 말고, 요청한 부분만 명확하게 반영하세요."
)

# 더 구체적인 유저 프롬프트
PROMPT_TEMPLATE = """
아래는 사용자의 질문과 관련된 코드 청크 및 프로젝트 구조, 그리고 각 코드의 메타데이터(파일명, 함수명, 클래스명, 라인, sha, 역할 태그)입니다.

[프로젝트 디렉토리 구조]
{directory_structure}

[코드 컨텍스트 및 메타데이터]
{context}

[질문]
{question}

위 코드, 메타데이터, 프로젝트 구조, 질문을 참고하여, 반드시 한글로, 예시와 함께, 친절하게 답변해 주세요.
- 답변에는 반드시 근거(파일명, 함수명, 클래스명, 라인, 역할 태그 등)를 명확히 포함하세요.
- 코드가 필요한 경우 코드 블록(```)과 한글 주석을 적극적으로 활용하세요.
- 단계별 설명, 표, 요약, 비교, 한계 등도 적극적으로 활용하세요.
- 프로젝트 구조와 코드의 역할(역할 태그)을 바탕으로, 전체 맥락과 흐름을 설명하세요.
- 질문이 역할 기반일 경우, 역할 태그와 가장 관련 있는 코드 근거를 들어 설명하세요.
- 모르는 부분이나 코드에 없는 정보는 추측하지 말고 "해당 정보는 코드에 없습니다"라고 답변하세요.
- 답변의 신뢰도를 높이기 위해, 항상 답변의 출처(파일명, 함수명, 역할 등)를 함께 제시하세요.
"""

MODIFY_PROMPT_TEMPLATE = """
아래는 사용자의 코드 수정 요청과 관련된 코드 청크 및 프로젝트 구조입니다.

[프로젝트 디렉토리 구조]
{directory_structure}

[코드 컨텍스트]
{context}

[수정 요청]
{request}

아래 형식으로 전체 코드를 수정해서 보여주세요.
// FILE: 파일명
<수정된 전체 코드>

- 반드시 한글 주석을 포함하세요.
- 프로젝트 구조에 대한 이해를 바탕으로 코드를 수정하세요.
- 불필요한 변경은 하지 말고, 요청한 부분만 명확하게 반영하세요.
- 코드 외 설명이 필요하면 코드 아래에 추가로 작성하세요.
"""

def parse_llm_code_response(llm_response):
    # // FILE: ... 또는 파일명: ... 패턴에서 파일명과 코드 추출
    m = re.search(r'// FILE: ([^\n]+)\n([\s\S]+)', llm_response)
    if m:
        return m.group(1).strip(), m.group(2).strip()
    m2 = re.search(r'파일명[:：]\s*([^\n]+)\n([\s\S]+)', llm_response)
    if m2:
        return m2.group(1).strip(), m2.group(2).strip()
    return None, llm_response.strip()

def handle_chat(session_id, message):
    # app.py의 sessions 데이터에서 세션 정보 확인
    from app import sessions
    print(f"[DEBUG] 현재 세션 ID: {session_id}")
    print(f"[DEBUG] 사용 가능한 세션 키: {list(sessions.keys())}")
    
    session_data = sessions.get(session_id, {})
    repo_path = f"./repos/{session_id}"
    
    # 세션 데이터가 없으면 오류 반환
    if not session_data:
        return {
            'answer': "세션 데이터가 없습니다. 새로운 레포지토리를 분석해주세요.",
            'error': "session_not_found"
        }
    
    print(f"[DEBUG] 세션 데이터 키: {list(session_data.keys())}")
    
    # 1. 질문 임베딩 생성
    print(f"[DEBUG] 질문 임베딩 생성 시작: '{message[:50]}...'")
    try:
        # OpenAI API 키 확인
        api_key = openai.api_key
        if not api_key:
            print("[ERROR] OpenAI API 키가 설정되지 않았습니다.")
            return {
                'answer': "OpenAI API 키가 설정되지 않았습니다.",
                'error': "api_key_missing"
            }
        print(f"[DEBUG] OpenAI API 키 확인: {api_key[:4]}...{api_key[-4:]}")
        
        # 임베딩 생성 시도
        print(f"[DEBUG] OpenAI 임베딩 API 호출 시도")
        embedding_response = openai.embeddings.create(
            input=message,
            model="text-embedding-3-small"
        )
        
        # 임베딩 결과 처리
        if not embedding_response or not embedding_response.data or not embedding_response.data[0].embedding:
            print(f"[ERROR] 임베딩 결과가 비어 있습니다: {embedding_response}")
            return {
                'answer': "임베딩 생성 중 오류가 발생했습니다: 임베딩 결과가 비어 있습니다.",
                'error': "empty_embedding"
            }
            
        embedding = embedding_response.data[0].embedding
        print(f"[DEBUG] 질문 임베딩 생성 성공 (차원: {len(embedding)})")
    except Exception as e:
        import traceback
        print(f"[ERROR] 질문 임베딩 생성 실패: {e}")
        traceback.print_exc()
        return {
            'answer': f"임베딩 생성 중 오류가 발생했습니다: {str(e)}",
            'error': "embedding_error"
        }

    # 2. ChromaDB에서 유사 코드 청크 검색
    try:
        # ChromaDB 클라이언트 상태 확인
        if not chroma_client:
            print("[ERROR] ChromaDB 클라이언트가 초기화되지 않았습니다.")
            return {
                'answer': "저장소 분석 데이터에 접근할 수 없습니다. 서버를 재시작하고 저장소를 다시 분석해주세요.",
                'error': "chroma_client_not_initialized"
            }
        
        # 컬렉션 이름 생성 및 조회 시도
        collection_name = f"repo_{session_id}"
        print(f"[DEBUG] ChromaDB 컬렉션 조회 시도: {collection_name}")
        
        # 컬렉션 목록 확인
        try:
            collections = chroma_client.list_collections()
            collection_names = [col.name for col in collections]
            print(f"[DEBUG] 사용 가능한 컬렉션 목록: {collection_names}")
        except Exception as e:
            import traceback
            print(f"[ERROR] ChromaDB 컬렉션 목록 조회 실패: {e}")
            traceback.print_exc()
            return {
                'answer': f"저장소 분석 데이터 접근 중 오류가 발생했습니다: {str(e)}",
                'error': "collection_list_error"
            }
        
        # 컬렉션 존재 여부 확인
        if collection_name not in collection_names:
            print(f"[ERROR] 컬렉션을 찾을 수 없음: {collection_name}")
            return {
                'answer': f"저장소 분석 데이터를 찾을 수 없습니다. 저장소를 다시 분석해주세요.",
                'error': "collection_not_found"
            }
        
        # 컬렉션 가져오기
        try:    
            collection = chroma_client.get_collection(name=collection_name)
            print(f"[DEBUG] 컬렉션 조회 성공: {collection_name}")
        except Exception as e:
            import traceback
            print(f"[ERROR] 컬렉션 가져오기 실패: {e}")
            traceback.print_exc()
            return {
                'answer': f"저장소 분석 데이터 접근 중 오류가 발생했습니다: {str(e)}",
                'error': "collection_access_error"
            }
        
        # 컬렉션 내 문서 수 확인
        try:
            collection_count = collection.count()
            print(f"[DEBUG] 컬렉션 내 문서 수: {collection_count}")
            if collection_count == 0:
                print(f"[WARNING] 컬렉션이 비어 있습니다: {collection_name}")
                return {
                    'answer': "저장소 분석 데이터가 비어 있습니다. 저장소를 다시 분석해주세요.",
                    'error': "empty_collection"
                }
        except Exception as e:
            import traceback
            print(f"[WARNING] 컬렉션 문서 수 확인 실패: {e}")
            traceback.print_exc()
            # 문서 수 확인 실패는 치명적이지 않을 수 있으므로 계속 진행
        
        # 유사 코드 청크 검색
        print(f"[DEBUG] 유사 코드 청크 검색 시작 (TOP_K={TOP_K})")
        try:
            results = collection.query(
                query_embeddings=[embedding],
                n_results=TOP_K
            )
            print(f"[DEBUG] 검색 결과 구조: {list(results.keys())}")
        except Exception as e:
            import traceback
            print(f"[ERROR] 유사 코드 청크 검색 실패: {e}")
            traceback.print_exc()
            return {
                'answer': f"코드 검색 중 오류가 발생했습니다: {str(e)}",
                'error': "query_error"
            }
        
        # 1. 질문 의도 태깅 (LLM)
        try:
            tag_prompt = f"아래 질문의 의도(원하는 코드 역할/기능)를 한글로 간단히 요약해줘.\n\n질문:\n{message}"
            tag_resp = openai.chat.completions.create(
                model="gpt-3.5-turbo",
                messages=[{"role": "user", "content": tag_prompt}],
                temperature=0.0,
                max_tokens=32
            )
            question_role_tag = tag_resp.choices[0].message.content.strip()
            print(f"[DEBUG] 질문 의도 태그: {question_role_tag}")
        except Exception as e:
            print(f"[WARNING] 질문 의도 태깅 실패: {e}")
            question_role_tag = ''
        # 2. role_tag 매칭 청크 우선 포함
        context_chunks = []
        if 'documents' in results and 'metadatas' in results and results['documents'][0] and results['metadatas'][0]:
            for doc, meta in zip(results['documents'][0], results['metadatas'][0]):
                role_tag = meta.get('role_tag', '')
                if question_role_tag and role_tag and (question_role_tag in role_tag or role_tag in question_role_tag):
                    meta_info = []
                    if meta.get('file_name'): meta_info.append(f"파일명: {meta['file_name']}")
                    if meta.get('function_name'): meta_info.append(f"함수: {meta['function_name']}")
                    if meta.get('class_name'): meta_info.append(f"클래스: {meta['class_name']}")
                    if meta.get('start_line') and meta.get('end_line'):
                        meta_info.append(f"라인: {meta['start_line']}~{meta['end_line']}")
                    if meta.get('sha'): meta_info.append(f"sha: {meta['sha']}")
                    meta_info.append(f"역할: {role_tag}")
                    context_chunks.append(f"[{'/'.join(meta_info)}]\n{doc}")
        # 3. 매칭된 청크가 부족하면 기존 방식으로 보충
        if len(context_chunks) < 3:
            for doc, meta in zip(results['documents'][0], results['metadatas'][0]):
                role_tag = meta.get('role_tag', '')
                meta_info = []
                if meta.get('file_name'): meta_info.append(f"파일명: {meta['file_name']}")
                if meta.get('function_name'): meta_info.append(f"함수: {meta['function_name']}")
                if meta.get('class_name'): meta_info.append(f"클래스: {meta['class_name']}")
                if meta.get('start_line') and meta.get('end_line'):
                    meta_info.append(f"라인: {meta['start_line']}~{meta['end_line']}")
                if meta.get('sha'): meta_info.append(f"sha: {meta['sha']}")
                if role_tag: meta_info.append(f"역할: {role_tag}")
                chunk_str = f"[{'/'.join(meta_info)}]\n{doc}"
                if chunk_str not in context_chunks:
                    context_chunks.append(chunk_str)
                if len(context_chunks) >= 5:
                    break
        # 4. 프롬프트에 컨텍스트 범위 안내
        context = '\n\n'.join(context_chunks)
        context = f"아래는 [파일/함수/클래스/라인/역할] 단위로 추출된 컨텍스트입니다.\n{context}"
        print(f"[DEBUG] 유사 코드 청크 {len(context_chunks)}개 찾음 (총 {len(context)} 문자)")
        
        # 검색된 파일 경로 로깅
        if 'metadatas' in results and results['metadatas'] and results['metadatas'][0]:
            paths = [meta.get('path', 'unknown') for meta in results['metadatas'][0]]
            print(f"[DEBUG] 검색된 파일 경로: {paths}")
            # 유사도 점수 로깅 (있는 경우)
            if 'distances' in results and results['distances'] and results['distances'][0]:
                distances = results['distances'][0]
                print(f"[DEBUG] 검색 유사도 점수: {distances}")
                # 파일 경로와 유사도 점수 매핑
                path_scores = list(zip(paths, distances))
                print(f"[DEBUG] 파일별 유사도 점수: {path_scores}")
        else:
            print("[WARNING] 검색 결과에 메타데이터가 없습니다.")

    except Exception as e:
        print(f"[ERROR] 코드 청크 검색 오류: {e}")
        return {
            'answer': f"코드 검색 중 오류가 발생했습니다: {str(e)}",
            'error': "search_error"
        }
    
    # 디렉토리 구조 확인
    directory_structure = session_data.get('directory_structure')
    
    if directory_structure:
        print(f"[DEBUG] 디렉토리 구조 정보 제공 (길이: {len(directory_structure)} 문자)")
    else:
        print("[DEBUG] 디렉토리 구조 정보가 없습니다.")
        directory_structure = "프로젝트 구조 정보가 없습니다. 파일 내용만 참고하여 응답하겠습니다."

    # 파일 전체 코드 요구 패턴 감지
    file_full_keywords = ["전체", "전체 코드", "전체내용", "전체 보여", "전체 출력"]
    is_full_file_request = any(kw in message for kw in file_full_keywords)
    scope = extract_scope_from_question(message)
    full_file_contexts = []
    if is_full_file_request and scope['file']:
        for fname in scope['file']:
            # 세션 데이터에서 파일 경로 찾기
            file_path = None
            for f in session_data.get('files', []):
                if f.get('file_name') and fname in f['file_name']:
                    file_path = f['path']
                    break
            if file_path:
                try:
                    with open(f"{repo_path}/{file_path}", 'r', encoding='utf-8') as f:
                        code = f.read()
                    full_file_contexts.append(f"// FILE: {file_path}\n{code}")
                except Exception as e:
                    print(f"[WARNING] 파일 전체 코드 로드 실패: {file_path}, {e}")

    # 청크 검색 결과와 파일 전체 내용 합치기
    if full_file_contexts:
        context = '\n\n'.join(full_file_contexts)
    else:
        # 기존 방식
        context = '\n\n'.join(context_chunks)
    context = f"아래는 [파일/함수/클래스/라인/역할] 단위로 추출된 컨텍스트입니다.\n{context}"
    
    # 검색된 파일 경로 로깅
    if 'metadatas' in results and results['metadatas'] and results['metadatas'][0]:
        paths = [meta.get('path', 'unknown') for meta in results['metadatas'][0]]
        print(f"[DEBUG] 검색된 파일 경로: {paths}")
        # 유사도 점수 로깅 (있는 경우)
        if 'distances' in results and results['distances'] and results['distances'][0]:
            distances = results['distances'][0]
            print(f"[DEBUG] 검색 유사도 점수: {distances}")
            # 파일 경로와 유사도 점수 매핑
            path_scores = list(zip(paths, distances))
            print(f"[DEBUG] 파일별 유사도 점수: {path_scores}")
    else:
        print("[WARNING] 검색 결과에 메타데이터가 없습니다.")

    # 3. LLM에 컨텍스트와 함께 전달하여 답변 생성
    try:
        # 프롬프트 생성
        prompt = PROMPT_TEMPLATE.format(
            context=context, 
            question=message,
            directory_structure=directory_structure
        )
        print("\n[LLM 프롬프트]\n" + prompt + "\n")  # 프롬프트 확인용 출력
        print(f"[DEBUG] 프롬프트 길이: {len(prompt)} 문자")
        
        # 프롬프트 길이 제한 확인
        if len(prompt) > 100000:  # OpenAI API의 토큰 제한을 고려한 값
            print(f"[WARNING] 프롬프트가 너무 깁니다. 컨텍스트 일부를 잘라냅니다.")
            # 컨텍스트 길이 제한
            max_context_length = 80000  # 적절한 길이로 조정
            truncated_context = context[:max_context_length] + "\n... (컨텍스트 길이 제한으로 인해 일부 내용이 생략되었습니다) ..."
            prompt = PROMPT_TEMPLATE.format(
                context=truncated_context, 
                question=message,
                directory_structure=directory_structure
            )
            print(f"[DEBUG] 수정된 프롬프트 길이: {len(prompt)} 문자")
        
        # LLM 호출
        print(f"[DEBUG] OpenAI API 호출 시작 (model=gpt-4o, temperature=0.2)")
        response = openai.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "system", "content": SYSTEM_PROMPT_QA},
                      {"role": "user", "content": prompt}],
            temperature=0.2,
            max_tokens=2048
        )
        
        # 응답 처리
        if not response or not response.choices or not response.choices[0].message:
            print(f"[ERROR] LLM 응답이 비어 있습니다: {response}")
            return {
                'answer': "응답 생성 중 오류가 발생했습니다. 다시 시도해주세요.",
                'error': "empty_response"
            }
        
        answer = response.choices[0].message.content.strip()
        print(f"[DEBUG] LLM 응답 성공 (길이: {len(answer)} 문자)")
        
        # 응답이 비어있는지 확인
        if not answer:
            print("[WARNING] LLM이 비어있는 응답을 리턴했습니다.")
            return {
                'answer': "질문에 대한 답변을 생성하지 못했습니다. 다른 질문을 시도해주세요.",
                'error': "empty_answer"
            }
        
        # 성공적인 응답 반환
        return {'answer': answer}
    except Exception as e:
        import traceback
        print(f"[ERROR] LLM 호출 오류: {e}")
        traceback.print_exc()
        return {
            'answer': f"응답 생성 중 오류가 발생했습니다: {str(e)}",
            'error': "llm_error"
        }

def handle_modify_request(session_id, message):
    # 세션 데이터 확인
    from app import sessions
    print(f"[DEBUG] 현재 세션 ID: {session_id}")
    print(f"[DEBUG] 사용 가능한 세션 키: {list(sessions.keys())}")
    
    session_data = sessions.get(session_id, {})
    repo_path = f"./repos/{session_id}"
    
    # 세션 데이터가 없으면 오류 반환
    if not session_data:
        return {
            'answer': "세션 데이터가 없습니다. 새로운 레포지토리를 분석해주세요.",
            'error': "session_not_found",
            'modified_code': "",
            'file_name': ""
        }
    
    print(f"[DEBUG] 세션 데이터 키: {list(session_data.keys())}")
    
    # 1단계: 청크 검색으로 관련 파일 식별
    try:
        # OpenAI API 키 확인
        api_key = openai.api_key
        if not api_key:
            print("[ERROR] OpenAI API 키가 설정되지 않았습니다.")
            return {
                'answer': "OpenAI API 키가 설정되지 않았습니다.",
                'error': "api_key_missing",
                'modified_code': "",
                'file_name': ""
            }
        print(f"[DEBUG] OpenAI API 키 확인: {api_key[:4]}...{api_key[-4:]}")
        
        # 임베딩 생성
        print(f"[DEBUG] 수정 요청 임베딩 생성 시작: '{message[:50]}...'")
        embedding_response = openai.embeddings.create(
            input=message,
            model="text-embedding-3-small"
        )
        
        # 임베딩 결과 처리
        if not embedding_response or not embedding_response.data or not embedding_response.data[0].embedding:
            print(f"[ERROR] 임베딩 결과가 비어 있습니다: {embedding_response}")
            return {
                'answer': "임베딩 생성 중 오류가 발생했습니다: 임베딩 결과가 비어 있습니다.",
                'error': "empty_embedding",
                'modified_code': "",
                'file_name': ""
            }
            
        embedding = embedding_response.data[0].embedding
        print(f"[DEBUG] 수정 요청 임베딩 생성 성공 (차원: {len(embedding)})")
        
        # ChromaDB 클라이언트 상태 확인
        if not chroma_client:
            print("[ERROR] ChromaDB 클라이언트가 초기화되지 않았습니다.")
            return {
                'answer': "저장소 분석 데이터에 접근할 수 없습니다. 서버를 재시작하고 저장소를 다시 분석해주세요.",
                'error': "chroma_client_not_initialized",
                'modified_code': "",
                'file_name': ""
            }
        
        # 컬렉션 이름 생성 및 조회 시도
        collection_name = f"repo_{session_id}"
        print(f"[DEBUG] ChromaDB 컬렉션 조회 시도: {collection_name}")
        
        # 컬렉션 존재 확인
        try:
            collections = chroma_client.list_collections()
            collection_names = [col.name for col in collections]
            print(f"[DEBUG] 사용 가능한 컬렉션 목록: {collection_names}")
            
            if collection_name not in collection_names:
                print(f"[ERROR] 컬렉션을 찾을 수 없음: {collection_name}")
                return {
                    'answer': "저장소 분석 데이터를 찾을 수 없습니다. 저장소를 다시 분석해주세요.",
                    'error': "collection_not_found",
                    'modified_code': "",
                    'file_name': ""
                }
        except Exception as e:
            import traceback
            print(f"[ERROR] ChromaDB 컬렉션 목록 조회 실패: {e}")
            traceback.print_exc()
            return {
                'answer': f"저장소 분석 데이터 접근 중 오류가 발생했습니다: {str(e)}",
                'error': "collection_list_error",
                'modified_code': "",
                'file_name': ""
            }
        
        # 컬렉션 가져오기
        try:
            collection = chroma_client.get_collection(name=collection_name)
            print(f"[DEBUG] 컬렉션 조회 성공: {collection_name}")
            
            # 컬렉션 내 문서 수 확인
            try:
                collection_count = collection.count()
                print(f"[DEBUG] 컬렉션 내 문서 수: {collection_count}")
                if collection_count == 0:
                    print(f"[WARNING] 컬렉션이 비어 있습니다: {collection_name}")
                    return {
                        'answer': "저장소 분석 데이터가 비어 있습니다. 저장소를 다시 분석해주세요.",
                        'error': "empty_collection",
                        'modified_code': "",
                        'file_name': ""
                    }
            except Exception as e:
                print(f"[WARNING] 컬렉션 문서 수 확인 실패: {e}")
                # 문서 수 확인 실패는 치명적이지 않을 수 있으므로 계속 진행
        except Exception as e:
            import traceback
            print(f"[ERROR] 컬렉션 가져오기 실패: {e}")
            traceback.print_exc()
            return {
                'answer': f"저장소 분석 데이터 접근 중 오류가 발생했습니다: {str(e)}",
                'error': "collection_access_error",
                'modified_code': "",
                'file_name': ""
            }
        
        # 유사 코드 청크 검색
        print(f"[DEBUG] 유사 코드 청크 검색 시작 (TOP_K={TOP_K})")
        try:
            results = collection.query(
                query_embeddings=[embedding],
                n_results=TOP_K
            )
            print(f"[DEBUG] 검색 결과 구조: {list(results.keys())}")
        except Exception as e:
            import traceback
            print(f"[ERROR] 유사 코드 청크 검색 실패: {e}")
            traceback.print_exc()
            return {
                'answer': f"코드 검색 중 오류가 발생했습니다: {str(e)}",
                'error': "query_error",
                'modified_code': "",
                'file_name': ""
            }
        
        # 검색 결과 유효성 검증
        if not results or 'metadatas' not in results or not results['metadatas'] or not results['metadatas'][0]:
            print(f"[WARNING] 검색 결과가 비어 있습니다")
            return {
                'answer': "질문과 관련된 코드를 찾을 수 없습니다. 다른 질문을 시도해보세요.",
                'error': "no_results",
                'modified_code': "",
                'file_name': ""
            }
        
        # 관련 파일 경로 추출
        related_files = set()
        for metadata in results['metadatas'][0]:
            if 'path' in metadata:
                related_files.add(metadata['path'])
            else:
                print(f"[WARNING] 메타데이터에 'path' 키가 없습니다: {metadata}")
        
        if not related_files:
            print("[WARNING] 관련 파일을 찾을 수 없습니다.")
            return {
                'answer': "질문과 관련된 코드 파일을 찾을 수 없습니다. 다른 질문을 시도해보세요.",
                'error': "no_related_files",
                'modified_code': "",
                'file_name': ""
            }
        
        print(f"[DEBUG] 관련 파일 경로: {related_files}")
        
        # 유사도 점수 로깅 (있는 경우)
        if 'distances' in results and results['distances'] and results['distances'][0]:
            distances = results['distances'][0]
            print(f"[DEBUG] 검색 유사도 점수: {distances}")
            
            # 파일 경로와 유사도 점수 매핑
            path_scores = []
            for i, metadata in enumerate(results['metadatas'][0]):
                if 'path' in metadata and i < len(distances):
                    path_scores.append((metadata['path'], distances[i]))
            
            if path_scores:
                print(f"[DEBUG] 파일별 유사도 점수: {path_scores}")

    except Exception as e:
        print(f"[ERROR] 코드 청크 검색 오류: {e}")
        return {
            'answer': "코드 검색 중 오류가 발생했습니다. 새로운 레포지토리를 분석해주세요.",
            'error': "search_error",
            'modified_code': "",
            'file_name': ""
        }
    
    # 관련 파일들의 전체 내용 로드
    print(f"[DEBUG] 관련 파일 {len(related_files)}개의 전체 내용 로드 시작")
    full_file_contents = []
    failed_files = []
    
    for file_path in related_files:
        print(f"[DEBUG] 파일 로드 시도: {file_path}")
        try:
            # 로컬 저장소 경로 확인
            local_file_path = f"{repo_path}/{file_path}"
            print(f"[DEBUG] 로컬 파일 경로: {local_file_path}")
            
            # 파일 존재 확인
            import os
            if not os.path.exists(local_file_path):
                print(f"[WARNING] 파일이 로컬에 존재하지 않음: {local_file_path}")
                raise FileNotFoundError(f"파일을 찾을 수 없습니다: {local_file_path}")
                
            # 로컬 저장소에서 파일 읽기
            with open(local_file_path, 'r', encoding='utf-8') as f:
                content = f.read()
                print(f"[DEBUG] 파일 로드 성공: {file_path} (길이: {len(content)} 문자)")
                full_file_contents.append(f"// FILE: {file_path}\n{content}")
        except UnicodeDecodeError as ude:
            print(f"[WARNING] 파일 인코딩 오류 ({file_path}): {ude}")
            try:
                # 다른 인코딩으로 시도
                with open(f"{repo_path}/{file_path}", 'r', encoding='latin-1') as f:
                    content = f.read()
                    print(f"[DEBUG] 파일 latin-1 인코딩으로 로드 성공: {file_path}")
                    full_file_contents.append(f"// FILE: {file_path}\n{content}")
            except Exception as e_inner:
                print(f"[ERROR] 다른 인코딩으로도 파일 읽기 실패 ({file_path}): {e_inner}")
                failed_files.append(file_path)
        except Exception as e:
            import traceback
            print(f"[ERROR] 파일 읽기 오류 ({file_path}): {e}")
            traceback.print_exc()
            
            # 읽기 실패 시 GitHub에서 직접 가져오기 시도
            try:
                print(f"[DEBUG] GitHub에서 파일 가져오기 시도: {file_path}")
                from github_analyzer import GitHubAnalyzer
                
                # 저장소 URL 확인
                repo_url = session_data.get('repo_url')
                if not repo_url:
                    print(f"[ERROR] 저장소 URL이 없습니다.")
                    failed_files.append(file_path)
                    continue
                    
                analyzer = GitHubAnalyzer(repo_url, session_data.get('token'), session_id)
                content = analyzer.fetch_file_content(file_path)
                
                if content:
                    print(f"[DEBUG] GitHub에서 파일 가져오기 성공: {file_path} (길이: {len(content)} 문자)")
                    full_file_contents.append(f"// FILE: {file_path}\n{content}")
                else:
                    print(f"[WARNING] GitHub에서 파일 가져오기 실패 (내용 없음): {file_path}")
                    failed_files.append(file_path)
            except Exception as e2:
                import traceback
                print(f"[ERROR] GitHub에서 파일 가져오기 오류 ({file_path}): {e2}")
                traceback.print_exc()
                failed_files.append(file_path)
    
    # 파일 로드 결과 요약
    print(f"[DEBUG] 총 {len(related_files)}개 파일 중 {len(full_file_contents)}개 로드 성공, {len(failed_files)}개 실패")
    if failed_files:
        print(f"[WARNING] 로드 실패한 파일들: {failed_files}")
        
    # 로드된 파일이 없는 경우 처리
    if not full_file_contents:
        print(f"[ERROR] 파일을 하나도 로드하지 못했습니다.")
        return {
            'answer': "관련 코드 파일을 로드하지 못했습니다. 저장소를 다시 분석해주세요.",
            'error': "file_load_error",
            'modified_code': "",
            'file_name': ""
        }
    
    # 청크 검색 결과도 함께 컨텍스트로 사용
    context_chunks = [doc for doc in results['documents'][0]]
    
    # 청크 검색 결과와 파일 전체 내용 합치기
    if full_file_contents:
        context = "\n\n=== 관련 파일 전체 내용 ===\n\n" + "\n\n---\n\n".join(full_file_contents)
        context += "\n\n=== 관련 코드 청크 ===\n\n" + "\n---\n".join(context_chunks)
    else:
        # 파일 전체 내용을 가져오지 못한 경우 청크만 사용
        context = "\n---\n".join(context_chunks)
    
    # 디렉토리 구조 가져오기
    directory_structure = session_data.get('directory_structure')
    
    if directory_structure:
        print(f"[DEBUG] 디렉토리 구조 정보 제공 (길이: {len(directory_structure)} 문자)")
    else:
        print("[DEBUG] 디렉토리 구조 정보가 없습니다.")
        directory_structure = "프로젝트 구조 정보가 없습니다. 파일 내용만 참고하여 응답하겠습니다."
    
    # 프롬프트 생성 및 LLM 호출
    try:
        # 프롬프트 생성
        prompt = MODIFY_PROMPT_TEMPLATE.format(
            context=context, 
            request=message,
            directory_structure=directory_structure
        )
        print("\n[LLM 프롬프트 - 코드수정]\n" + prompt + "\n")  # 프롬프트 확인용 출력
        print(f"[DEBUG] 코드수정 프롬프트 길이: {len(prompt)} 문자")
        
        # 프롬프트 길이 제한 확인
        if len(prompt) > 100000:  # OpenAI API의 토큰 제한을 고려한 값
            print(f"[WARNING] 코드수정 프롬프트가 너무 깁니다. 컨텍스트 일부를 잘라냅니다.")
            # 컨텍스트 길이 제한
            max_context_length = 80000  # 적절한 길이로 조정
            truncated_context = context[:max_context_length] + "\n... (컨텍스트 길이 제한으로 인해 일부 내용이 생략되었습니다) ..."
            prompt = MODIFY_PROMPT_TEMPLATE.format(
                context=truncated_context, 
                request=message,
                directory_structure=directory_structure
            )
            print(f"[DEBUG] 수정된 코드수정 프롬프트 길이: {len(prompt)} 문자")
        
        # LLM 호출
        print(f"[DEBUG] 코드수정용 OpenAI API 호출 시작 (model=gpt-4o, temperature=0.2, max_tokens=4096)")
        response = openai.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "system", "content": SYSTEM_PROMPT_MODIFY},
                      {"role": "user", "content": prompt}],
            temperature=0.2,
            max_tokens=4096
        )
        
        # 응답 처리
        if not response or not response.choices or not response.choices[0].message:
            print(f"[ERROR] 코드수정 LLM 응답이 비어 있습니다: {response}")
            return {
                'answer': "코드 수정 중 오류가 발생했습니다. 다시 시도해주세요.",
                'error': "empty_response",
                'modified_code': "",
                'file_name': ""
            }
        
        llm_code = response.choices[0].message.content.strip()
        print(f"[DEBUG] 코드수정 LLM 응답 성공 (길이: {len(llm_code)} 문자)")
        
        # 응답이 비어있는지 확인
        if not llm_code:
            print("[WARNING] 코드수정 LLM이 비어있는 응답을 리턴했습니다.")
            return {
                'answer': "코드 수정을 생성하지 못했습니다. 다른 수정 요청을 시도해주세요.",
                'error': "empty_code",
                'modified_code': "",
                'file_name': ""
            }
        
        # 파일명과 코드 분리
        file_name, code = parse_llm_code_response(llm_code)
        print(f"[DEBUG] 파싱된 파일명: '{file_name or '(none)'}', 코드 길이: {len(code)} 문자")
        
        # 코드가 비어있는지 확인
        if not code:
            print("[WARNING] 파싱된 코드가 비어 있습니다.")
            return {
                'answer': "코드 수정을 생성하지 못했습니다. 다른 수정 요청을 시도해주세요.",
                'error': "empty_parsed_code",
                'modified_code': "",
                'file_name': ""
            }
        
        # 성공적인 응답 반환
        # LLM이 제안한 변경사항을 세션에 임시 저장합니다.
        sessions[session_id]['suggested_change'] = {
            'file_name': file_name or '',
            'modified_code': code
        }
        # 세션 파일에 저장
        from app import save_sessions # app 모듈에서 import
        save_sessions(sessions)
        print(f"[DEBUG] 제안된 변경사항 세션에 저장 완료: {file_name or '(none)'}")

        return {'modified_code': code, 'file_name': file_name or ''}
    except Exception as e:
        import traceback
        print(f"[ERROR] 코드수정 LLM 호출 오류: {e}")
        traceback.print_exc()
        return {
            'answer': f"코드 수정 중 오류가 발생했습니다: {str(e)}",
            'error': "llm_error",
            'modified_code': "",
            'file_name': ""
        }

def apply_changes(session_id, file_name, new_content):
    """코드 변경사항을 저장소에 적용하고 커밋/푸시하는 함수"""
    print(f"[DEBUG] 코드 변경사항 적용 시작 (session_id: {session_id})")
    
    # 입력값 검증
    if not session_id:
        print("[ERROR] 세션 ID가 제공되지 않았습니다.")
        return {'result': '에러: 세션 ID가 제공되지 않았습니다.'}

    # 세션에서 제안된 변경사항 가져오기
    from app import sessions
    session_data = sessions.get(session_id, {})
    if not session_data:
        print("[ERROR] 유효하지 않은 세션 ID입니다.")
        return {'result': '에러: 유효하지 않은 세션 ID입니다.'}
        
    suggested_change = session_data.get('suggested_change')
    if not suggested_change:
        print("[ERROR] 적용할 제안된 변경사항이 없습니다.")
        return {'result': '에러: 적용할 제안된 변경사항이 없습니다. 코드 수정을 먼저 요청해주세요.'}

    # 제안된 변경사항에서 파일명과 내용 추출
    apply_file_name = suggested_change.get('file_name')
    apply_content = suggested_change.get('modified_code')

    if not apply_file_name or not apply_content:
        print("[ERROR] 제안된 변경사항의 파일명 또는 내용이 비어 있습니다.")
        # 세션에서 해당 정보 삭제 (다음 요청을 위해) - 필요에 따라 주석 처리 가능
        # del sessions[session_id]['suggested_change']
        # from app import save_sessions
        # save_sessions(sessions)
        return {'result': '에러: 적용할 코드 내용이 비어 있습니다.'}

    print(f"[DEBUG] 적용할 파일: {apply_file_name}, 내용 길이: {len(apply_content)}")

    # 저장소 경로 확인
    repo_path = f"./repos/{session_id}"
    print(f"[DEBUG] 저장소 경로: {repo_path}")
    
    import os
    if not os.path.exists(repo_path):
        print(f"[ERROR] 저장소 경로가 존재하지 않습니다: {repo_path}")
        # 세션에서 해당 정보 삭제 (다음 요청을 위해)
        # del sessions[session_id]['suggested_change']
        # from app import save_sessions
        # save_sessions(sessions)
        return {'result': f'에러: 저장소 경로가 존재하지 않습니다: {repo_path}'}
    
    # CodeModifier 인스턴스 생성
    modifier = CodeModifier()

    commit_msg = "AI 코드 자동 수정" # 기본 커밋 메시지
    # TODO: 사용자로부터 커밋 메시지를 입력받는 로직 추가 필요

    try:
        # 변경사항 커밋
        print(f"[DEBUG] 코드 변경사항 커밋 시작: {apply_file_name}")
        commit_result = modifier.commit_changes(repo_path, apply_file_name, apply_content, commit_msg)
        
        if not commit_result['success']:
            print(f"[ERROR] 커밋 실패: {commit_result['error']}")
            # 세션에서 해당 정보 삭제 (다음 요청을 위해)
            # del sessions[session_id]['suggested_change']
            # from app import save_sessions
            # save_sessions(sessions)
            return {'result': f"에러: 커밋 실패 - {commit_result['error']}"}

        print(commit_result['message']) # 커밋 성공 메시지 출력

        # 변경사항 푸시
        print("[DEBUG] 코드 변경사항 푸시 시작")
        push_result = modifier.push_changes(repo_path)
        
        if not push_result['success']:
            print(f"[ERROR] 푸시 실패: {push_result['error']}")
            # 세션에서 해당 정보 삭제 (다음 요청을 위해)
            # del sessions[session_id]['suggested_change']
            # from app import save_sessions
            # save_sessions(sessions)
            return {'result': f"경고: 커밋은 성공했으나 푸시 실패 - {push_result['error']}"} # 푸시 실패는 경고로 처리 가능

        print(push_result['message']) # 푸시 성공 메시지 출력

        # 세션에서 제안된 변경사항 삭제 (성공적으로 적용 완료)
        if 'suggested_change' in sessions[session_id]:
            del sessions[session_id]['suggested_change']
            from app import save_sessions
            save_sessions(sessions)
            print("[DEBUG] 세션에서 제안된 변경사항 삭제 완료")

        return {'result': '코드가 성공적으로 커밋 및 푸시되었습니다.'}
        
    except Exception as e:
        import traceback
        print(f"[ERROR] 코드 변경사항 적용(커밋/푸시) 실패: {e}")
        traceback.print_exc()
        # 오류 발생 시에도 세션에서 제안된 변경사항 삭제 (다음 요청을 위해)
        if session_id in sessions and 'suggested_change' in sessions[session_id]:
             del sessions[session_id]['suggested_change']
             from app import save_sessions
             save_sessions(sessions)
             print("[DEBUG] 오류 발생 후 세션에서 제안된 변경사항 삭제 완료")
        return {'result': f'에러: 코드 적용 중 오류 발생 - {str(e)}'}

def extract_scope_from_question(question: str):
    """
    질문에서 파일명, 함수명, 클래스명, 디렉토리명 등 범위 키워드 추출
    """
    file_match = re.findall(r'([\w_\-/]+\.\w+)', question)
    func_match = re.findall(r'(\w+) ?함수', question)
    class_match = re.findall(r'(\w+) ?클래스', question)
    dir_match = re.findall(r'([\w_\-/]+)/', question)
    return {
        'file': file_match,
        'function': func_match,
        'class': class_match,
        'directory': dir_match
    } 
