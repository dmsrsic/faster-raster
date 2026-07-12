# Adapter Security Model

Network preview is disabled by default and requires --allow-network plus --allow-preview and exact plan approval. Requests are HTTPS, host-allowlisted, bounded by dimensions and bytes, reject private or loopback destinations where practical, redact credentials, avoid arbitrary URL fetching, and fail closed on unsupported media types or redirects.
