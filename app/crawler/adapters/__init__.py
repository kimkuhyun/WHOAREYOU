from app.crawler.adapters.base import JobAdapter, JobStub, normalize_company_name
from app.crawler.adapters.jobkorea import JobkoreaAdapter
from app.crawler.adapters.saramin import SaraminAdapter
from app.crawler.adapters.wanted import WantedAdapter

ADAPTERS: dict[str, type[JobAdapter]] = {
    "saramin": SaraminAdapter,
    "jobkorea": JobkoreaAdapter,
    "wanted": WantedAdapter,
}

__all__ = [
    "ADAPTERS",
    "JobAdapter",
    "JobStub",
    "normalize_company_name",
    "SaraminAdapter",
    "JobkoreaAdapter",
    "WantedAdapter",
]
