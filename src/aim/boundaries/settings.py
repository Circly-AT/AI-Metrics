"""The one env reader. Same DB_* names as AI-Pipeline-V3's `.env`, so both services can
share one env file on a host."""

from pathlib import Path
from typing import Literal, Self
from urllib.parse import quote_plus

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

TlsPosition = Literal["verified", "off"]

_TLS_FIELDS = ("db_ssl_ca", "db_ssl_cert", "db_ssl_key")
"""Authority the server certificate is checked against, this client's certificate, its key."""


class Settings(BaseSettings):
    # "ignore": a shared .env carries keys for other consumers (MLflow, RabbitMQ, ...).
    # `hide_input_in_errors`: a refused value must not print the password next to the reason.
    model_config = SettingsConfigDict(extra="ignore", frozen=True, hide_input_in_errors=True)

    db_host: str
    db_port: int = 3306
    db_username: str
    db_password: str = Field(repr=False)
    data_db: str = "circly_data_handling"

    db_tls: TlsPosition
    """REQUIRED, no default (same rule as AI-Pipeline-V3, operator ruling 2026-09-12: "we
    only ever take verified SSL"). Handed no `ssl` argument, pymysql does NOT fall back to
    cleartext: it negotiates TLS and verifies nothing -- encrypted, but open to anyone who
    can sit between client and server, and indistinguishable from a verified connection
    from the inside. So the position has to be stated: `verified` with certificates, or
    `off` for an endpoint that speaks no TLS (a local test DB). A missing or misspelled
    DB_TLS is a startup refusal, never a silent fallback."""

    # Three FILE PATHS, handed to the driver, never read here. Required together under
    # `verified`, forbidden under `off` (see `_the_switch_and_its_material_agree`).
    db_ssl_ca: str | None = None
    db_ssl_cert: str | None = None
    db_ssl_key: str | None = None

    poll_interval_seconds: float = Field(default=30.0, gt=0)
    stale_grace_seconds: int = Field(default=3600, gt=0)

    @field_validator(*_TLS_FIELDS, mode="after")
    @classmethod
    def _blank_means_absent(cls, value: str | None) -> str | None:
        """`.env.example` carries the three keys empty; a copied template means "not set",
        not "three empty paths"."""
        return (value or "").strip() or None

    @model_validator(mode="after")
    def _the_switch_and_its_material_agree(self) -> Self:
        """Every refusal here is about a connection that would otherwise work and look fine:
        - `off` with certificates: `ssl_disabled` wins in pymysql, the certificates would be
          silently ignored.
        - `verified` with only some paths: the driver would connect unverified.
        - `verified` with a path that is not a file: fails at the first connection, which in
          a daemon can be long after start -- checked now instead."""
        configured = [name for name in _TLS_FIELDS if getattr(self, name) is not None]
        if self.db_tls == "off":
            if configured:
                raise ValueError(
                    f"DB_TLS=off while {', '.join(n.upper() for n in configured)} set: "
                    "remove the certificate paths or use DB_TLS=verified"
                )
            return self
        missing = [name.upper() for name in _TLS_FIELDS if getattr(self, name) is None]
        if missing:
            raise ValueError(
                f"DB_TLS=verified needs DB_SSL_CA, DB_SSL_CERT and DB_SSL_KEY; missing: "
                f"{', '.join(missing)}"
            )
        for name in _TLS_FIELDS:
            if not Path(getattr(self, name)).is_file():
                raise ValueError(f'{name.upper()}="{getattr(self, name)}" is not a file')
        return self

    @property
    def db_tls_args(self) -> dict[str, object]:
        """The driver's TLS arguments -- one of exactly two shapes, never empty (an empty
        mapping is the unverified mode). `check_hostname`/`verify_mode` are stated, not left
        to pymysql's defaults: verification is the whole point and must not change silently
        with a driver upgrade."""
        if self.db_tls == "off":
            return {"ssl_disabled": True}
        return {
            "ssl": {
                "ca": self.db_ssl_ca,
                "cert": self.db_ssl_cert,
                "key": self.db_ssl_key,
                "check_hostname": True,
                "verify_mode": True,
            }
        }

    @property
    def db_url(self) -> str:
        return (
            f"mysql+pymysql://{quote_plus(self.db_username)}:{quote_plus(self.db_password)}"
            f"@{self.db_host}:{self.db_port}/{self.data_db}?charset=utf8mb4"
        )
