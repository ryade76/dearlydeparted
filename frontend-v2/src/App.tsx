import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { Layout } from './components/Layout'
import { ChatView } from './components/ChatView'

const queryClient = new QueryClient()

export function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <Layout>
        <ChatView />
      </Layout>
    </QueryClientProvider>
  )
}
