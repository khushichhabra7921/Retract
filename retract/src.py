"""Source identifiers. A source id is a plain `kind:id` string."""


def user(user_id: str) -> str:
    return f"user:{user_id}"


def conversation(conversation_id: str) -> str:
    return f"conversation:{conversation_id}"


def doc(doc_id: str) -> str:
    return f"doc:{doc_id}"


def tool(tool_id: str) -> str:
    return f"tool:{tool_id}"
