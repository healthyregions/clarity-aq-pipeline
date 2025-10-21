from config import log, IS_MINIO, S3_ENDPOINT_URL, S3_ACCESS_KEY, S3_SECRET_KEY
from config import S3_REGION, S3_STORAGE_CLASS, S3_BUCKET_NAME
import sys

import s3fs


# Build an S3 client using the given credentials + endpoint
s3_client = s3fs.S3FileSystem(anon=False, key=S3_ACCESS_KEY, secret=S3_SECRET_KEY, client_kwargs={
    # Use custom endpoint URL for self-hosted (e.g. MinIO)
    'endpoint_url': S3_ENDPOINT_URL,
    # NOTE: StorageClass & Region are ignored when using MinIO
}) if IS_MINIO else s3fs.S3FileSystem(anon=False, key=S3_ACCESS_KEY, secret=S3_SECRET_KEY, s3_additional_kwargs={
    # provide a StorageClass to use for uploads (default: INTELLIGENT_TIERING)
    'StorageClass': S3_STORAGE_CLASS
}, client_kwargs={
    # For default endpoint, assume AWS S3
    # for this case, we will want to specify a Region & StorageClass
    'region_name': S3_REGION,
})


# globals: S3_ENDPOINT_URL, IS_MINIO, S3_REGION, s3_client
class S3API(object):
    def list_folders(self):
        return s3_client.ls(path=f'{S3_BUCKET_NAME}/')

    def push_to_s3(self, local_path: str, remote_path: str):
        # Determine destination within S3
        s3_label = 'MinIO' if IS_MINIO else 'AWS S3'
        s3_extra = S3_ENDPOINT_URL if IS_MINIO else S3_REGION

        try:
            # Notify user that upload is about to start
            log.debug(f'Uploading to {s3_label} ({s3_extra}): {local_path}')

            # Upload the local file to S3
            s3_client.put(local_path, remote_path)

            # Notify user that upload is about has completed
            log.info(f'Uploaded successfully to {s3_label} ({s3_extra}): {local_path} -> {remote_path}')
        except Exception as ex:
            formatted = f'{local_path} -> {remote_path}'
            log.error(f'Failed to upload file to {s3_label}: {formatted} - {str(ex)}')
            sys.exit(100)
