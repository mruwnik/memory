const AuthError = ({ error, onRetry }: { error: string, onRetry: () => void }) => {
    return (
        <div className="error">
            <h2>Authentication Error</h2>
            <p>{error}</p>
            <button onClick={onRetry}>
                Try Again
            </button>
        </div>
    )
}

export default AuthError 