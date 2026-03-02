import { put } from '@vercel/blob';

export type UploadedBlobMetadata = {
  url: string;
  pathname: string;
  contentType: string;
  size: number;
};

export async function uploadToBlob(file: File, pathname: string, token: string): Promise<UploadedBlobMetadata> {
  const blob = await put(pathname, file, {
    access: 'private',
    token,
  });

  return {
    url: blob.url,
    pathname: blob.pathname,
    contentType: file.type || 'application/octet-stream',
    size: file.size,
  };
}
