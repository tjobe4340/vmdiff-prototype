import React from 'react';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import App from './App';

let mockTreeProps: any;
jest.mock('antd', () => {
  const Box = ({ children }: any) => <div>{children}</div>;
  return {
    Tree: { DirectoryTree: (props: any) => {
      mockTreeProps = props;
      return <><button onClick={() => props.onSelect(['A'])}>A</button>
        <button onClick={() => props.onSelect(['B'])}>B</button>
        <button onClick={() => props.onSelect([])}>Clear</button></>;
    } },
    Layout: Object.assign(Box, { Header: Box, Content: Box, Sider: Box }),
    Typography: Object.assign(Box, { Title: Box }), Space: Box
  };
});
jest.mock('@ant-design/icons', () => ({ FolderOutlined: () => null, ExperimentOutlined: () => null }));
jest.mock('crypto-hash', () => ({ sha1: async (key: string) => key }));
jest.mock('diff2html', () => ({ html: (text: string) => `<pre>${text}</pre>` }));

const response = (data: unknown, status = 200) => ({ ok: status < 400, status, json: async () => data }) as Response;
const deferred = () => {
  let resolve!: (response: Response) => void;
  const promise = new Promise<Response>(done => { resolve = done; });
  return { promise, resolve };
};

beforeEach(() => {
  global.fetch = jest.fn(async () => response([]));
});

test('renders an empty comparison without a content crash', async () => {
  render(<App />);
  fireEvent.click(screen.getByText('A'));
  await waitFor(() => expect(screen.queryByRole('status')).not.toBeInTheDocument());
  expect(screen.getByText('No content diff for this item.')).toBeInTheDocument();
});

test.each([response(null), response(null, 404)])('handles unchanged parents and older static exports', async result => {
  (fetch as jest.Mock).mockImplementation(async (url: string) => url.includes('/diff/') ? result : response([]));
  render(<App />);
  fireEvent.click(screen.getByText('A'));
  await waitFor(() => expect(screen.queryByRole('status')).not.toBeInTheDocument());
  expect(screen.queryByRole('alert')).not.toBeInTheDocument();
});

test('latest selection wins even if responses arrive out of order', async () => {
  const first = deferred(), second = deferred();
  (fetch as jest.Mock).mockImplementation((url: string) => url.endsWith('/diff/A') ? first.promise : url.endsWith('/diff/B') ? second.promise : Promise.resolve(response([])));
  render(<App />);
  fireEvent.click(screen.getByText('A'));
  fireEvent.click(screen.getByText('B'));
  await act(async () => { second.resolve(response(['new selection'])); });
  expect(screen.getByText('new selection')).toBeInTheDocument();
  await act(async () => { first.resolve(response(['stale selection'])); });
  expect(screen.queryByText('stale selection')).not.toBeInTheDocument();
});

test('clearing selection invalidates an in-flight diff and does not fetch undefined', async () => {
  const pending = deferred();
  (fetch as jest.Mock).mockImplementation((url: string) => url.includes('/diff/') ? pending.promise : Promise.resolve(response([])));
  render(<App />);
  fireEvent.click(screen.getByText('A'));
  fireEvent.click(screen.getByText('Clear'));
  await act(async () => { pending.resolve(response(['stale selection'])); });
  expect(screen.queryByText('stale selection')).not.toBeInTheDocument();
  expect((fetch as jest.Mock).mock.calls.some(([url]) => url.includes('undefined'))).toBe(false);
});

test('reports an HTTP failure and allows selection retry', async () => {
  (fetch as jest.Mock).mockImplementation(async (url: string) => url.includes('/diff/') ? response({}, 500) : response([]));
  render(<App />);
  fireEvent.click(screen.getByText('A'));
  expect(await screen.findByRole('alert')).toHaveTextContent('Could not load this diff');
  (fetch as jest.Mock).mockResolvedValue(response(['retried content']));
  fireEvent.click(screen.getByText('A'));
  expect(await screen.findByText('retried content')).toBeInTheDocument();
});

test('folder loading waits for the response and propagates failed loads for retry', async () => {
  const pending = deferred();
  (fetch as jest.Mock).mockImplementation((url: string) => url.includes('/children/') ? pending.promise : Promise.resolve(response([])));
  render(<App />);
  let settled = false;
  let load!: Promise<void>;
  await act(async () => { load = mockTreeProps.loadData({ key: 'folder' }); load.then(() => { settled = true; }, () => { settled = true; }); });
  expect(settled).toBe(false);
  await act(async () => { pending.resolve(response({}, 500)); await expect(load).rejects.toThrow(); });
  expect(settled).toBe(true);
  expect(screen.getByRole('alert')).toHaveTextContent('Could not load this folder');
  (fetch as jest.Mock).mockResolvedValue(response([]));
  await act(async () => { await mockTreeProps.loadData({ key: 'folder' }); });
  expect(screen.queryByRole('alert')).not.toBeInTheDocument();
});
