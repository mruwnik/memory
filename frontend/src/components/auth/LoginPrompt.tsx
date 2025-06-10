const LoginPrompt = ({ onLogin }: { onLogin: () => void }) => {
    return (
        <div className="login-prompt">
            <h1>Memory App</h1>
            <p>Please log in to access your memory database</p>
            <button onClick={onLogin} className="login-btn">
                Log In
            </button>
        </div>
    )
}

export default LoginPrompt 