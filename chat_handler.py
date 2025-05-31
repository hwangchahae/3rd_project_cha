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
    "질문에 대해 코드 컨텍스트를 바탕으로 정확하고, 예시와 함께, 한글로 상세하게 설명하세요. "
    "필요하다면 코드 블록, 주석, 표, 단계별 설명을 적극적으로 활용하세요. "
    "답변이 불확실할 경우에는 추측하지 말고, 모르는 부분은 명확히 밝혀주세요."
)
SYSTEM_PROMPT_MODIFY = (
    "당신은 코드 리팩터링 및 버그 수정에 특화된 AI입니다. "
    "사용자의 요청을 코드 컨텍스트와 함께 분석하여, 전체 코드를 수정해줍니다. "
    "수정된 코드는 반드시 // FILE: 파일명\n<전체 코드> 형식으로, 주석과 함께, 한글로 친절하게 작성하세요. "
    "불필요한 변경은 하지 말고, 요청한 부분만 명확하게 반영하세요."
)

# 더 구체적인 유저 프롬프트
PROMPT_TEMPLATE = """
아래는 사용자의 질문과 관련된 코드 청크입니다.

[코드 컨텍스트]
{context}

[질문]
{question}

위 코드와 질문을 참고하여, 반드시 한글로, 예시와 함께, 친절하게 답변해 주세요.
- 코드가 필요한 경우 코드 블록(```)과 주석을 활용하세요.
- 단계별 설명, 표, 요약 등도 적극적으로 활용하세요.
- 모르는 부분은 추측하지 말고 "해당 정보는 코드에 없습니다"라고 답변하세요.
"""

MODIFY_PROMPT_TEMPLATE = """
아래는 사용자의 코드 수정 요청과 관련된 코드 청크입니다.

[코드 컨텍스트]
{context}

[수정 요청]
{request}

아래 형식으로 전체 코드를 수정해서 보여주세요.
// FILE: 파일명
<수정된 전체 코드>

- 반드시 한글 주석을 포함하세요.
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
    # 1. 질문 임베딩 생성
    embedding = openai.embeddings.create(
        input=message,
        model="text-embedding-3-small"
    ).data[0].embedding

    # 2. ChromaDB에서 유사 코드 청크 검색
    collection = chroma_client.get_collection(name=f"repo_{session_id}")
    results = collection.query(
        query_embeddings=[embedding],
        n_results=TOP_K
    )
    # 유사 코드 청크 추출
    context_chunks = [doc for doc in results['documents'][0]]
    context = "\n---\n".join(context_chunks)

    # 3. LLM에 컨텍스트와 함께 전달하여 답변 생성
    prompt = PROMPT_TEMPLATE.format(context=context, question=message)
    print("\n[LLM 프롬프트]\n" + prompt + "\n")  # 프롬프트 확인용 출력
    response = openai.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "system", "content": SYSTEM_PROMPT_QA},
                  {"role": "user", "content": prompt}],
        temperature=0.2,
        max_tokens=2048
    )
    answer = response.choices[0].message.content.strip()
    return {'answer': answer}

def handle_modify_request(session_id, message):
    embedding = openai.embeddings.create(
        input=message,
        model="text-embedding-3-small"
    ).data[0].embedding
    collection = chroma_client.get_collection(name=f"repo_{session_id}")
    results = collection.query(
        query_embeddings=[embedding],
        n_results=TOP_K
    )
    context_chunks = [doc for doc in results['documents'][0]]
    context = "\n---\n".join(context_chunks)
    prompt = MODIFY_PROMPT_TEMPLATE.format(context=context, request=message)
    print("\n[LLM 프롬프트 - 코드수정]\n" + prompt + "\n")  # 프롬프트 확인용 출력
    response = openai.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "system", "content": SYSTEM_PROMPT_MODIFY},
                  {"role": "user", "content": prompt}],
        temperature=0.2,
        max_tokens=4096
    )
    llm_code = response.choices[0].message.content.strip()
    file_name, code = parse_llm_code_response(llm_code)
    return {'modified_code': code, 'file_name': file_name or ''}

def apply_changes(session_id, file_name, new_content):
    repo_path = f"./repos/{session_id}"
    commit_msg = "AI 코드 자동 수정"
    try:
        create_branch_and_commit(repo_path, "ai-fix", file_name, new_content, commit_msg)
        return {'result': '코드가 적용되었습니다.'}
    except Exception as e:
        return {'result': f'에러: {str(e)}'} 