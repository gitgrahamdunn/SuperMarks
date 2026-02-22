import type { IncomingHttpHeaders, IncomingMessage } from 'node:http';
import { request as httpRequest } from 'node:http';
import { request as httpsRequest } from 'node:https';

type VercelRequest = IncomingMessage & {
  query: Record<string, string | string[] | undefined>;
  url?: string;
  method?: string;
};

type VercelResponse = {
  status: (statusCode: number) => VercelResponse;
  setHeader: (name: string, value: string | string[]) => void;
  send: (body: Buffer | string) => void;
  end: (body?: Buffer | string) => void;
};

const BACKEND_ORIGIN = process.env.BACKEND_ORIGIN || 'https://super-marks-2-backend.vercel.app';
const MAX_BODY_BYTES = 15 * 1024 * 1024;

export const config = {
  api: {
    bodyParser: false,
  },
};

function getPathParts(req: VercelRequest): string[] {
  const pathValue = req.query.path;
  if (Array.isArray(pathValue)) return pathValue;
  if (typeof pathValue === 'string' && pathValue.length > 0) return [pathValue];
  return [];
}

function collectBody(req: IncomingMessage): Promise<Buffer> {
  return new Promise((resolve, reject) => {
    const chunks: Buffer[] = [];
    let totalSize = 0;

    req.on('data', (chunk: Buffer) => {
      totalSize += chunk.length;
      if (totalSize > MAX_BODY_BYTES) {
        reject(new Error('PAYLOAD_TOO_LARGE'));
        req.destroy();
        return;
      }
      chunks.push(chunk);
    });

    req.on('end', () => resolve(Buffer.concat(chunks)));
    req.on('error', reject);
  });
}

function filterForwardHeaders(headers: IncomingHttpHeaders): Record<string, string> {
  const forwarded: Record<string, string> = {};

  for (const [key, value] of Object.entries(headers)) {
    if (!value) continue;

    const lowerKey = key.toLowerCase();
    if (lowerKey === 'host' || lowerKey === 'connection' || lowerKey === 'content-length') {
      continue;
    }

    forwarded[key] = Array.isArray(value) ? value.join(', ') : value;
  }

  return forwarded;
}

export default async function handler(req: VercelRequest, res: VercelResponse): Promise<void> {
  const method = req.method || 'GET';
  const incomingUrl = new URL(req.url || '/', 'http://localhost');
  const pathParts = getPathParts(req);
  const requestedPath = pathParts.join('/');

  if (requestedPath === 'proxy-health') {
    res.setHeader('content-type', 'application/json; charset=utf-8');
    res.status(200).send(JSON.stringify({ ok: true, backend: BACKEND_ORIGIN }));
    return;
  }

  const querystring = incomingUrl.search;
  const target = requestedPath === 'openapi.json'
    ? `${BACKEND_ORIGIN}/openapi.json`
    : `${BACKEND_ORIGIN}/api/${requestedPath}${querystring}`;
  const targetUrl = new URL(target.replace(/([^:]\/)(\/+)/g, '$1$2'));

  let body = Buffer.alloc(0);
  if (method !== 'GET' && method !== 'HEAD') {
    try {
      body = await collectBody(req);
    } catch (error) {
      if (error instanceof Error && error.message === 'PAYLOAD_TOO_LARGE') {
        res.status(413).send('Payload too large');
        return;
      }

      res.status(400).send('Invalid request body');
      return;
    }
  }

  const targetHeaders = filterForwardHeaders(req.headers);
  if (body.length > 0) {
    targetHeaders['content-length'] = String(body.length);
  }

  const requestImpl = targetUrl.protocol === 'http:' ? httpRequest : httpsRequest;

  await new Promise<void>((resolve) => {
    const proxyReq = requestImpl(
      {
        protocol: targetUrl.protocol,
        hostname: targetUrl.hostname,
        port: targetUrl.port || undefined,
        method,
        path: `${targetUrl.pathname}${targetUrl.search}`,
        headers: targetHeaders,
      },
      (proxyRes) => {
        const responseChunks: Buffer[] = [];
        let hasContentType = false;

        for (const [headerName, headerValue] of Object.entries(proxyRes.headers)) {
          const lowerHeader = headerName.toLowerCase();
          if (!headerValue || lowerHeader === 'transfer-encoding') {
            continue;
          }

          if (lowerHeader === 'content-type') {
            hasContentType = true;
          }

          res.setHeader(headerName, Array.isArray(headerValue) ? headerValue : String(headerValue));
        }

        if (!hasContentType) {
          res.setHeader('content-type', 'application/octet-stream');
        }

        proxyRes.on('data', (chunk: Buffer) => responseChunks.push(chunk));
        proxyRes.on('end', () => {
          res.status(proxyRes.statusCode || 502).end(Buffer.concat(responseChunks));
          resolve();
        });
      },
    );

    proxyReq.on('error', () => {
      res.setHeader('content-type', 'application/json; charset=utf-8');
      res.status(502).send(JSON.stringify({ detail: 'Upstream proxy request failed' }));
      resolve();
    });

    if (body.length > 0) {
      proxyReq.write(body);
    }

    proxyReq.end();
  });
}
