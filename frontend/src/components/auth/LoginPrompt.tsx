const LoginPrompt = ({ onLogin }: { onLogin: () => void }) => {
    return (
        <div className="flex justify-center items-center min-h-screen flex-col text-center p-8 bg-gradient-to-br from-primary to-secondary text-white">
            <h1 className="text-4xl font-bold mb-4">Memory App</h1>
            <p className="text-lg mb-8 opacity-90 max-w-md">Please log in to access your memory database</p>
            <button
                onClick={onLogin}
                className="bg-white text-primary border-none py-4 px-8 rounded-xl text-lg font-semibold cursor-pointer transition-all shadow-lg hover:-translate-y-0.5 hover:shadow-xl"
            >
                Log In
            </button>
        </div>
    )
}

export default LoginPrompt
