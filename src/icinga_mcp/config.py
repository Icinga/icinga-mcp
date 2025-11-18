from __future__ import annotations

from pydantic import AliasChoices, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables and .env."""

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", case_sensitive=False, extra="allow"
    )

    # Upstream Icinga Web base URL and auth (accept both prefixed and generic env names)
    base_url: str = Field(
        "https://monitoring.example.com/icingaweb2",
        validation_alias=AliasChoices("ICINGA_WEB_BASE_URL", "BASE_URL"),
    )
    username: str = Field(
        "api-user", validation_alias=AliasChoices("ICINGA_WEB_USERNAME", "USERNAME")
    )
    password: SecretStr = Field(
        SecretStr("change-me"),
        validation_alias=AliasChoices("ICINGA_WEB_PASSWORD", "PASSWORD"),
    )
    # TLS
    verify_tls: bool = Field(
        True, validation_alias=AliasChoices("ICINGA_WEB_VERIFY_TLS", "VERIFY_TLS")
    )
    ca_bundle: str | None = Field(
        None, validation_alias=AliasChoices("ICINGA_WEB_CA_BUNDLE", "CA_BUNDLE")
    )

    # HTTP behavior
    request_timeout: float = Field(
        15.0, validation_alias=AliasChoices("HTTP_REQUEST_TIMEOUT", "REQUEST_TIMEOUT")
    )
    max_retries: int = Field(3, validation_alias=AliasChoices("HTTP_MAX_RETRIES", "MAX_RETRIES"))
    retry_backoff: float = Field(
        0.5, validation_alias=AliasChoices("HTTP_RETRY_BACKOFF", "RETRY_BACKOFF")
    )

    # REST server
    rest_host: str = Field("127.0.0.1", validation_alias=AliasChoices("REST_HOST"))
    rest_port: int = Field(8080, validation_alias=AliasChoices("REST_PORT"))
    # REST authentication (Bearer only)
    # Global auth is enabled if REST_REQUIRE_API_KEY=true OR REST_BEARER_TOKEN is non-empty.
    # Only supported header: Authorization: Bearer <token> (from REST_BEARER_TOKEN).
    rest_auth_required: bool = Field(
        False, validation_alias=AliasChoices("REST_REQUIRE_API_KEY", "REQUIRE_API_KEY")
    )
    rest_bearer_token: SecretStr | None = Field(
        None, validation_alias=AliasChoices("REST_BEARER_TOKEN", "BEARER_TOKEN")
    )

    # Default summary field sets (CSV, override via env)
    host_summary_fields: str = Field(
        "name,state.code,state.name,state.is_problem,last_check,last_state_change,acknowledged,in_downtime,output",
        validation_alias="HOST_SUMMARY_FIELDS",
    )
    service_summary_fields: str = Field(
        "host.name,name,state.code,state.name,state.is_problem,last_check,last_state_change,acknowledged,in_downtime,output",
        validation_alias="SERVICE_SUMMARY_FIELDS",
    )
    # Comments summary fields (no internal id; use 'name' instead)
    comment_summary_fields: str = Field(
        "time,host,service,name,author,text,is_persistent,is_sticky,expire_time",
        validation_alias="COMMENT_SUMMARY_FIELDS",
    )
    # Downtimes summary fields (compact, mirrors comments where applicable)
    downtime_summary_fields: str = Field(
        "time,host,service,name,author,text,start_time,end_time,is_fixed,is_in_effect",
        validation_alias="DOWNTIME_SUMMARY_FIELDS",
    )
    # Notifications summary fields (compact like comments; keep payload lean)
    notification_summary_fields: str = Field(
        "time,host,service,type,state,previous_hard_state,users_notified,author,text",
        validation_alias="NOTIFICATION_SUMMARY_FIELDS",
    )
    # History event summaries (separate defaults for host vs service)
    host_event_summary_fields: str = Field(
        "time,type,host,state_code,state_name,previous_state_code,previous_state_name,author,text,output",
        validation_alias="HOST_EVENT_SUMMARY_FIELDS",
    )
    service_event_summary_fields: str = Field(
        "time,type,host,service,service_state_code,service_state_name,previous_state_code,previous_state_name,host_state_code,host_state_name,author,text,output",
        validation_alias="SERVICE_EVENT_SUMMARY_FIELDS",
    )
    # Back-compat generic event summary fields (used if explicitly requested)
    event_summary_fields: str = Field(
        "time,type,state.code,state.name,author,text,output",
        validation_alias="EVENT_SUMMARY_FIELDS",
    )

    # Endpoint mapping (relative to icingaweb2 root)
    endpoints: dict[str, str] = {
        "hosts": "icingadb/hosts",
        "services": "icingadb/services",
        "downtimes": "icingadb/downtimes",
        "comments": "icingadb/comments",
        "notifications": "icingadb/notifications",
        "comments_delete": "icingadb/comments/delete",
        "downtimes_delete": "icingadb/downtimes/delete",
        # Group overviews (plural)
        "hostgroups": "icingadb/hostgroups",
        "servicegroups": "icingadb/servicegroups",
        # Group details (singular)
        "hostgroup": "icingadb/hostgroup",
        "servicegroup": "icingadb/servicegroup",
        # History
        "host_history": "icingadb/host/history",
        "service_history": "icingadb/service/history",
        # add-comment
        "host_add_comment": "icingadb/host/add-comment",
        "service_add_comment": "icingadb/service/add-comment",
        # check-now
        "host_check_now": "icingadb/host/check-now",
        "service_check_now": "icingadb/service/check-now",
        # schedule-downtime (host/service endpoints)
        "host_schedule_downtime": "icingadb/host/schedule-downtime",
        "service_schedule_downtime": "icingadb/service/schedule-downtime",
        # acknowledge (host/service endpoints)
        "host_acknowledge": "icingadb/host/acknowledge",
        "service_acknowledge": "icingadb/service/acknowledge",
        "host_remove_acknowledgement": "icingadb/host/remove-acknowledgement",
        "service_remove_acknowledgement": "icingadb/service/remove-acknowledgement",
    }

    def endpoint(self, key: str) -> str:
        return self.endpoints[key]

    def get_host_summary_fields(self) -> list[str]:
        return [p.strip() for p in self.host_summary_fields.split(",") if p.strip()]

    def get_service_summary_fields(self) -> list[str]:
        return [p.strip() for p in self.service_summary_fields.split(",") if p.strip()]

    def get_comment_summary_fields(self) -> list[str]:
        return [p.strip() for p in self.comment_summary_fields.split(",") if p.strip()]

    def get_downtime_summary_fields(self) -> list[str]:
        return [p.strip() for p in self.downtime_summary_fields.split(",") if p.strip()]

    def get_event_summary_fields(self) -> list[str]:
        return [p.strip() for p in self.event_summary_fields.split(",") if p.strip()]

    def get_host_event_summary_fields(self) -> list[str]:
        return [p.strip() for p in self.host_event_summary_fields.split(",") if p.strip()]

    def get_service_event_summary_fields(self) -> list[str]:
        return [p.strip() for p in self.service_event_summary_fields.split(",") if p.strip()]

    def get_notification_summary_fields(self) -> list[str]:
        return [p.strip() for p in self.notification_summary_fields.split(",") if p.strip()]


def load_settings() -> Settings:
    return Settings()
