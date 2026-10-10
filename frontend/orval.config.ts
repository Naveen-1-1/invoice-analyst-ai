import { defineConfig } from 'orval'

// Typed TanStack Query hooks from the backend's OpenAPI description (make gen-api writes openapi.json first)
export default defineConfig({
  api: {
    input: './openapi.json',
    output: {
      target: './src/api/generated.ts',
      client: 'react-query',
      httpClient: 'fetch',
      mode: 'single',
      prettier: false,
      override: {
        mutator: { path: './src/api/fetcher.ts', name: 'customFetch' },
        fetch: { includeHttpResponseReturnType: false },
      },
    },
  },
})
