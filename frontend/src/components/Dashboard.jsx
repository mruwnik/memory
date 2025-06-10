import { Link } from 'react-router-dom'
import { useMCP } from '@/hooks/useMCP'

const Dashboard = ({ onLogout }) => {
    const { listNotes } = useMCP()

    return (
        <div className="app">
            <header className="app-header">
                <h1>Memory App</h1>
                <button onClick={onLogout} className="logout-btn">
                    Logout
                </button>
            </header>

            <main className="app-main">
                <div className="welcome">
                    <h2>Welcome to your Memory Database!</h2>
                    <p>You are successfully authenticated.</p>
                    <p>Access token is stored in cookies and ready for API calls.</p>
                </div>

                <div className="features">
                    <Link to="/ui/search" className="feature-card">
                        <h3>🔍 Search</h3>
                        <p>Search through your knowledge base</p>
                    </Link>

                    <div className="feature-card" onClick={async () => console.log(await listNotes())}>
                        <h3>📝 Notes</h3>
                        <p>Create and manage your notes</p>
                    </div>

                    <div className="feature-card">
                        <h3>🤖 AI Assistant</h3>
                        <p>Chat with your memory-enhanced AI</p>
                    </div>
                </div>
            </main>
        </div>
    )
}

export default Dashboard 