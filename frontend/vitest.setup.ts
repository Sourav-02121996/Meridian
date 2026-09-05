import '@testing-library/jest-dom/vitest';
import { cleanup } from '@testing-library/react';
import { afterEach } from 'vitest';

afterEach(() => {
  cleanup();
});

// recharts' <ResponsiveContainer> observes its element size; jsdom has no
// ResizeObserver implementation.
class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}
// @ts-expect-error -- test-only global stub, not a spec-accurate polyfill.
globalThis.ResizeObserver ??= ResizeObserverStub;
