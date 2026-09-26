# models/azure_profile.py
#
# Ported near-verbatim from ND3X-public/src/models/azure_profile.py, minus
# org_id/project_id (single-tenant). Named, reusable Azure identities: a
# msal_bundle (~/.azure files), a service_principal, or a bearer token —
# Fernet-encrypted at rest — so several identities can live side by side and
# be synced on demand to the LabX host or into a lab container.
from __future__ import annotations

from sqlalchemy import Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from db.database import Base

# entra_app: een EIGEN app-registratie waar de gebruiker zich met een
# device-code bij aanmeldt. Nodig voor alles wat delegated moet werken en dat
# de Azure CLI niet mag — Work IQ weigert de CLI met AADSTS65002, en zijn
# permissies bestaan alleen als delegated, dus een service principal helpt daar
# ook niet. Zie services/azure/entra_app_login.py.
# uami_federated: de managed identity van een klant gebruiken vanaf een server
# die NIET in Azure draait. Een UAMI-token komt normaal van het
# metadata-endpoint ín een Azure-resource; workload identity federation draait
# dat om — op de UAMI staat een federated credential met een issuer en een
# subject, en wie een getekend token van die issuer kan tonen, krijgt een token
# als die identiteit. LabX is dan zelf die issuer (twee statische bestanden
# publiek, de privésleutel hier). Zie services/azure/uami_federated.py.
AZURE_PROFILE_KINDS = ("msal_bundle", "service_principal", "bearer", "entra_app",
                       "uami_federated")
AZURE_SYNC_TARGETS = ("host", "lab")


class AzureProfile(Base):
    __tablename__ = "azure_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False, default="msal_bundle")
    # Fernet-encrypted JSON payload; shape depends on kind (see AZURE_PROFILE_KINDS).
    secret_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    identity_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[str] = mapped_column(String(64), nullable=False)
    updated_at: Mapped[str] = mapped_column(String(64), nullable=False)

    __table_args__ = (Index("idx_azure_profiles_name", "name"),)
