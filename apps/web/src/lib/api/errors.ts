import { ApiError } from "@/lib/api/client";

function normalizeUserFacingMessage(message: string): string {
  const normalizedMessage = message.toLowerCase();

  if (
    normalizedMessage.includes("clerk session token verification failed") ||
    normalizedMessage.includes("invalid_clerk_token") ||
    normalizedMessage.includes("bad_record_mac")
  ) {
    return "Your sign-in session could not be verified. Refresh the page or sign in again.";
  }

  if (
    normalizedMessage.includes("openrouter embeddings request failed") ||
    normalizedMessage.includes("\"user not found\"")
  ) {
    return "The configured OpenRouter API key is being rejected by the embeddings endpoint. Update the OpenRouter key or account setup and try again.";
  }

  return message;
}

export function getDisplayErrorMessage(error: unknown, fallbackMessage: string) {
  if (error instanceof ApiError) {
    const displayMessage = normalizeUserFacingMessage(error.message);

    const suffixes: string[] = [];
    if (error.provider) {
      suffixes.push(`Provider: ${error.provider}.`);
    }
    if (error.code) {
      suffixes.push(`Code: ${error.code}.`);
    }
    if (error.requestId) {
      suffixes.push(`Request ID: ${error.requestId}.`);
    }
    if (error.retryable) {
      suffixes.push("This request can be retried.");
    }
    return [displayMessage, ...suffixes].join(" ").trim();
  }

  if (error instanceof Error && error.message.trim()) {
    return normalizeUserFacingMessage(error.message);
  }

  return fallbackMessage;
}
