import { Link } from 'react-router-dom'
import { useMCP } from '@/hooks/useMCP'

const Dashboard = ({ onLogout }) => {
    const { listNotes } = useMCP()

    return (
        <div className="min-h-screen flex flex-col">
            <header className="bg-white border-b border-slate-200 px-8 py-4 flex justify-between items-center shadow-sm">
                <h1 className="text-primary text-2xl font-semibold">Memory App</h1>
                <button
                    onClick={onLogout}
                    className="bg-slate-50 text-slate-600 border border-slate-200 py-2 px-4 rounded-md text-sm cursor-pointer transition-all hover:bg-slate-100 hover:text-slate-800"
                >
                    Logout
                </button>
            </header>

            <main className="flex-1 p-8 mx-auto w-full max-w-6xl">
                <div className="text-center mb-12 p-8 bg-white rounded-xl shadow-md">
                    <h2 className="text-slate-800 text-3xl mb-4 font-semibold">Welcome to your Memory Database!</h2>
                    <p className="text-gray-600 text-base">You are successfully authenticated.</p>
                </div>

                <div className="grid grid-cols-[repeat(auto-fit,minmax(300px,1fr))] gap-6 mt-8">
                    <Link to="/ui/search" className="bg-white p-8 rounded-xl shadow-md text-center transition-all cursor-pointer no-underline text-inherit block hover:-translate-y-0.5 hover:shadow-lg">
                        <h3 className="text-slate-800 text-xl mb-2 font-semibold">Search</h3>
                        <p className="text-gray-600 text-base">Search through your knowledge base</p>
                    </Link>

                    <Link to="/ui/sources" className="bg-white p-8 rounded-xl shadow-md text-center transition-all cursor-pointer no-underline text-inherit block hover:-translate-y-0.5 hover:shadow-lg">
                        <h3 className="text-slate-800 text-xl mb-2 font-semibold">Sources</h3>
                        <p className="text-gray-600 text-base">Manage email, GitHub, RSS feeds, and Google Drive</p>
                    </Link>

                    <Link to="/ui/calendar" className="bg-white p-8 rounded-xl shadow-md text-center transition-all cursor-pointer no-underline text-inherit block hover:-translate-y-0.5 hover:shadow-lg">
                        <h3 className="text-slate-800 text-xl mb-2 font-semibold">Calendar</h3>
                        <p className="text-gray-600 text-base">View upcoming events from your calendars</p>
                    </Link>

                    <Link to="/ui/tasks" className="bg-white p-8 rounded-xl shadow-md text-center transition-all cursor-pointer no-underline text-inherit block hover:-translate-y-0.5 hover:shadow-lg">
                        <h3 className="text-slate-800 text-xl mb-2 font-semibold">Tasks</h3>
                        <p className="text-gray-600 text-base">Manage your todos and tasks</p>
                    </Link>

                    <Link to="/ui/metrics" className="bg-white p-8 rounded-xl shadow-md text-center transition-all cursor-pointer no-underline text-inherit block hover:-translate-y-0.5 hover:shadow-lg">
                        <h3 className="text-slate-800 text-xl mb-2 font-semibold">Metrics</h3>
                        <p className="text-gray-600 text-base">System health, task performance, and API usage</p>
                    </Link>

                    <Link to="/ui/telemetry" className="bg-white p-8 rounded-xl shadow-md text-center transition-all cursor-pointer no-underline text-inherit block hover:-translate-y-0.5 hover:shadow-lg">
                        <h3 className="text-slate-800 text-xl mb-2 font-semibold">Telemetry</h3>
                        <p className="text-gray-600 text-base">Claude Code usage, tokens, costs, and sessions</p>
                    </Link>

                    <Link to="/ui/jobs" className="bg-white p-8 rounded-xl shadow-md text-center transition-all cursor-pointer no-underline text-inherit block hover:-translate-y-0.5 hover:shadow-lg">
                        <h3 className="text-slate-800 text-xl mb-2 font-semibold">Jobs</h3>
                        <p className="text-gray-600 text-base">View background job status and retry failed jobs</p>
                    </Link>

                    <Link to="/ui/logs" className="bg-white p-8 rounded-xl shadow-md text-center transition-all cursor-pointer no-underline text-inherit block hover:-translate-y-0.5 hover:shadow-lg">
                        <h3 className="text-slate-800 text-xl mb-2 font-semibold">Docker Logs</h3>
                        <p className="text-gray-600 text-base">View API and worker container logs</p>
                    </Link>

                    <Link to="/ui/polls" className="bg-white p-8 rounded-xl shadow-md text-center transition-all cursor-pointer no-underline text-inherit block hover:-translate-y-0.5 hover:shadow-lg">
                        <h3 className="text-slate-800 text-xl mb-2 font-semibold">Polls</h3>
                        <p className="text-gray-600 text-base">Schedule meetings with availability polls</p>
                    </Link>

                    <div className="bg-white p-8 rounded-xl shadow-md text-center transition-all cursor-pointer hover:-translate-y-0.5 hover:shadow-lg" onClick={async () => console.log(await listNotes())}>
                        <h3 className="text-slate-800 text-xl mb-2 font-semibold">Notes</h3>
                        <p className="text-gray-600 text-base">Create and manage your notes</p>
                    </div>

                    <div className="bg-white p-8 rounded-xl shadow-md text-center transition-all cursor-pointer hover:-translate-y-0.5 hover:shadow-lg">
                        <h3 className="text-slate-800 text-xl mb-2 font-semibold">AI Assistant</h3>
                        <p className="text-gray-600 text-base">Chat with your memory-enhanced AI</p>
                    </div>
                </div>
            </main>
        </div>
    )
}

export default Dashboard
