"""Launcher identity preparation without long-lived cloud credentials."""

from dander.identity.aws_google import (
    FargateIdentityError,
    google_client_options,
    launcher_identity,
    prepare_fargate_google_identity,
    prepare_fargate_task_credentials,
    prepare_launcher_identity,
)
from dander.identity.azure_google import (
    AzureContainerAppsIdentityError,
    prepare_azure_google_identity,
)

__all__ = [
    "AzureContainerAppsIdentityError",
    "FargateIdentityError",
    "google_client_options",
    "launcher_identity",
    "prepare_fargate_google_identity",
    "prepare_fargate_task_credentials",
    "prepare_launcher_identity",
    "prepare_azure_google_identity",
]
