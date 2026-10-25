from app.models.llm_usage import LLMUsage
from sqlmodel import Session


class LLMUsageRepository:

    def create(
        self,
        session: Session,
        usage: LLMUsage,
    ) -> LLMUsage:

        session.add(usage)
        session.commit()
        session.refresh(usage)

        return usage


llm_usage_repository = LLMUsageRepository()