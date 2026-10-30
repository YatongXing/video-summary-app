"""Private S3 original assets. Never issues public or signed URLs."""
import os
import time
import boto3
from boto3.s3.transfer import TransferConfig
from botocore.config import Config
from botocore.exceptions import ClientError

def client():
    return boto3.client('s3',endpoint_url=os.environ['S3_ENDPOINT_URL'],aws_access_key_id=os.environ['S3_ACCESS_KEY'],aws_secret_access_key=os.environ['S3_SECRET_KEY'],region_name='us-east-1',config=Config(connect_timeout=10,read_timeout=60,retries={'max_attempts':2},s3={'addressing_style':'path'}))
def bucket(): return os.getenv('S3_BUCKET','video-summary-private')
def put_original(path,key):
    s3=client()
    try: s3.head_bucket(Bucket=bucket())
    except ClientError as error:
        if str(error.response['Error']['Code']) not in ('404','NoSuchBucket','NotFound'): raise
        try: s3.create_bucket(Bucket=bucket())
        except ClientError as create_error:
            if create_error.response['Error']['Code'] not in ('BucketAlreadyOwnedByYou','BucketAlreadyExists'): raise
    deadline=time.monotonic()+1800
    def progress(_bytes):
        if time.monotonic()>deadline: raise TimeoutError('Object upload timeout')
    s3.upload_file(str(path),bucket(),key,Callback=progress,Config=TransferConfig(multipart_threshold=16*1024*1024,multipart_chunksize=16*1024*1024,use_threads=False))
def delete_original(key):
    try: client().delete_object(Bucket=bucket(),Key=key)
    except ClientError as error:
        if str(error.response['Error']['Code']) not in ('404','NoSuchBucket','NoSuchKey','NotFound'): raise
