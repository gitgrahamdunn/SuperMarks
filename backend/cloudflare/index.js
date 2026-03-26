import { Container } from "@cloudflare/containers";

export class SupermarksApiContainer extends Container {
  defaultPort = 8080;
  sleepAfter = "10m";
}

function buildContainerEnvVars(env) {
  const envVars = {};

  for (const [key, value] of Object.entries(env)) {
    if (typeof value !== "string") continue;
    if (
      key === "APP_VERSION"
      || key === "BACKEND_API_KEY"
      || key === "DATABASE_URL"
      || key.startsWith("SUPERMARKS_")
      || key.startsWith("OPENAI_")
    ) {
      envVars[key] = value;
    }
  }

  if (!("SUPERMARKS_ENV" in envVars)) envVars.SUPERMARKS_ENV = "production";
  if (!("SUPERMARKS_MANAGED_RUNTIME_ENVIRONMENT" in envVars)) envVars.SUPERMARKS_MANAGED_RUNTIME_ENVIRONMENT = "1";
  if (!("SUPERMARKS_STORAGE_BACKEND" in envVars)) envVars.SUPERMARKS_STORAGE_BACKEND = "s3";
  if (!("SUPERMARKS_S3_REGION" in envVars)) envVars.SUPERMARKS_S3_REGION = "auto";
  if (!("SUPERMARKS_SERVE_FRONTEND" in envVars)) envVars.SUPERMARKS_SERVE_FRONTEND = "0";

  return envVars;
}

async function getPrimaryContainer(env) {
  const container = env.SUPERMARKS_API.getByName("primary");
  await container.startAndWaitForPorts({
    startOptions: {
      envVars: buildContainerEnvVars(env),
    },
  });
  return container;
}

export default {
  async fetch(request, env) {
    const container = await getPrimaryContainer(env);
    return container.fetch(request);
  },
};
