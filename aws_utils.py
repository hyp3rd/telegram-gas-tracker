"""AWS Utils and helper."""

import asyncio
import os
import time

import boto3
from botocore.exceptions import ClientError

from core import SingletonMeta
from enums import Env
from tracker.config import ConfigHandler


class AWSUtils(metaclass=SingletonMeta):
    """AWSUtils class"""

    def __init__(self):
        self.config = ConfigHandler()

    @staticmethod
    def get_secret(secret_name: str, region_name: str = "eu-central-1") -> str:
        """Get a secret value from AWS Secrets Manager."""
        # secret_name = "prod/telegram-gas-tracker/AWS_SECRET_ACCESS_KEY"
        # region_name = "eu-central-1"

        # Create a Secrets Manager client
        session = boto3.session.Session()
        client = session.client(service_name="secretsmanager", region_name=region_name)

        try:
            get_secret_value_response = client.get_secret_value(SecretId=secret_name)
        except ClientError as e:
            # For a list of exceptions thrown, see
            # https://docs.aws.amazon.com/secretsmanager/latest/apireference/API_GetSecretValue.html
            raise e

        return get_secret_value_response["SecretString"]

    @staticmethod
    def get_secret_value(secret_string: str) -> str:
        """Get a secret value from AWS Secrets Manager given the payload's SecretString"""
        if not secret_string:
            raise ValueError("Empty AWS Secret.")

        return secret_string.split('"')[3]

    async def ensure_credentials_file(self, home_dir: str = "/root"):
        """Ensure the AWS credentials are loaded."""
        lock = asyncio.Lock()
        await lock.acquire()
        try:
            # wait that the aws credentials are loaded
            for _ in range(self.config.aws_credentials_timeout):
                if os.path.isfile(f"{home_dir}/.aws/credentials"):
                    return
                time.sleep(5)

            raise TimeoutError("Timeout waiting for the AWS credentials")
        finally:
            lock.release()

    @staticmethod
    def load_credentials():
        """Load AWS credentials from environment variables."""
        aws_access_key_id = os.getenv("AWS_ACCESS_KEY_ID")
        aws_secret_access_key = os.getenv("AWS_SECRET_ACCESS_KEY")

        if not aws_access_key_id or not aws_secret_access_key:
            raise ValueError("AWS credentials not found in environment variables.")

        if os.getenv("CLOUD_PROVIDER") == Env.AWS.value:
            aws_access_key_id = AWSUtils.get_secret_value(aws_access_key_id)
            aws_secret_access_key = AWSUtils.get_secret_value(aws_secret_access_key)

        return aws_access_key_id, aws_secret_access_key

    async def generate_credentials_file(self, home_dir: str = "/root", attempt=0):
        """Generate the AWS credentials file."""
        aws_access_key_id, aws_secret_access_key = self.load_credentials()

        if not aws_access_key_id or not aws_secret_access_key:
            # wait that the aws credentials are loaded
            while attempt <= self.config.aws_credentials_timeout:
                print(
                    f"Waiting to load the AWS credentials ({attempt}/{self.config.aws_credentials_timeout})"
                )
                attempt = attempt + 1
                await asyncio.sleep(1)
                await self.generate_credentials_file(home_dir, attempt)

            raise ValueError("AWS credentials not loaded from environment variables")

        aws_config_path = f"{home_dir}/.aws"
        os.makedirs(aws_config_path, exist_ok=True)

        # create the credentials file
        with open(f"{aws_config_path}/credentials", "w", encoding="utf-8") as f:
            f.write(
                f"[default]\naws_access_key_id = {aws_access_key_id}\naws_secret_access_key = {aws_secret_access_key}\n"  # pylint: disable=line-too-long
            )
            f.close()

    @staticmethod
    def is_aws_environment() -> bool:
        """Check if the environment is AWS."""
        return os.getenv("CLOUD_PROVIDER") == Env.AWS.value
