import React from 'react';
import ReactDOM from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import Site from './Site';
import './index.css';

const client = new QueryClient({ defaultOptions: { queries: { staleTime: 15_000, retry: 1 } } });
ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <QueryClientProvider client={client}>
      <Site />
    </QueryClientProvider>
  </React.StrictMode>,
);
