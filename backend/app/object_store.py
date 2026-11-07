"""Private S3 original assets. Never issues public or signed URLs."""
from app import maintenance
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
    maintenance.ensure_open()
    s3=client()
    try: s3.head_bucket(Bucket=bucket())
    except ClientError as error:
        if str(error.response['Error']['Code']) not in ('404','NoSuchBucket','NotFound'): raise
        try: s3.create_bucket(Bucket=bucket())
        except ClientError as create_error:
            if create_error.response['Error']['Code'] not in ('BucketAlreadyOwnedByYou','BucketAlreadyExists'): raise
    deadline=time.monotonic()+1800
    def progress(_bytes):
        maintenance.ensure_open()
        if time.monotonic()>deadline: raise TimeoutError('Object upload timeout')
    if s3.get_bucket_versioning(Bucket=bucket()).get('Status') in ('Enabled','Suspended'):
        raise ValueError('Versioned content storage is not supported')
    maintenance.ensure_open()
    s3.upload_file(str(path),bucket(),key,Callback=progress,Config=TransferConfig(multipart_threshold=16*1024*1024,multipart_chunksize=16*1024*1024,use_threads=False))
    maintenance.ensure_open()
def delete_original(key):
    maintenance.ensure_open()
    try: client().delete_object(Bucket=bucket(),Key=key)
    except ClientError as error:
        if str(error.response['Error']['Code']) not in ('404','NoSuchBucket','NoSuchKey','NotFound'): raise


def backend_id():
    import hashlib
    from urllib.parse import urlsplit
    u=urlsplit(os.environ['S3_ENDPOINT_URL'])
    if u.username or u.password or u.query or u.fragment:raise ValueError('Invalid endpoint')
    normalized=u.scheme.lower()+'://'+u.netloc.lower()+u.path.rstrip('/')
    return 'S3:'+hashlib.sha256(normalized.encode()).hexdigest()

def cleanup_client():
    return boto3.client('s3',endpoint_url=os.environ['S3_ENDPOINT_URL'],aws_access_key_id=os.environ['S3_ACCESS_KEY'],aws_secret_access_key=os.environ['S3_SECRET_KEY'],region_name='us-east-1',config=Config(connect_timeout=3,read_timeout=10,retries={'total_max_attempts':1},s3={'addressing_style':'path'}))
