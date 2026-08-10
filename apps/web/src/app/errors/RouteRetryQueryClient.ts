import type { QueryClient } from "@tanstack/react-query";

export interface RouteRetryQueryClientScope {
  client: QueryClient;
  release: () => void;
}

type DefaultQueryOptions = QueryClient["defaultQueryOptions"];

/**
 * Give one route-retry commit a local query-options view without mutating the application client.
 * The proxy delegates every cache, mutation, default, and lifecycle operation to the source client;
 * only stale-query refetch-on-mount is suppressed until the retry commit has mounted.
 */
export function createRouteRetryQueryClientScope(source: QueryClient): RouteRetryQueryClientScope {
  let suppressRefetchOnMount = true;
  const sourceDefaultQueryOptions = source.defaultQueryOptions.bind(source) as DefaultQueryOptions;
  const retryDefaultQueryOptions = ((
    options: Parameters<DefaultQueryOptions>[0],
  ): ReturnType<DefaultQueryOptions> => {
    const defaulted = sourceDefaultQueryOptions(options);
    if (!suppressRefetchOnMount) return defaulted;
    return { ...defaulted, refetchOnMount: false };
  }) as DefaultQueryOptions;
  const delegatedProperties = new Map<PropertyKey, unknown>([
    ["defaultQueryOptions", retryDefaultQueryOptions],
  ]);

  const client = new Proxy(source, {
    get(target, property) {
      if (delegatedProperties.has(property)) return delegatedProperties.get(property);

      const value: unknown = Reflect.get(target, property, target);
      if (typeof value !== "function") return value;

      const delegated = value.bind(target) as unknown;
      delegatedProperties.set(property, delegated);
      return delegated;
    },
  });

  return {
    client,
    release: () => {
      suppressRefetchOnMount = false;
    },
  };
}
