const AuthError = ({ error, onRetry }: { error: string, onRetry: () => void }) => {
    return (
        <div className="flex justify-center items-center min-h-screen flex-col text-center p-8">
            <h2 className="text-[var(--color-danger)] mb-4 text-xl font-semibold">Authentication Error</h2>
            <p className="text-gray-600 mb-8 max-w-md">{error}</p>
            <button
                onClick={onRetry}
                className="bg-primary text-white border-none py-3 px-6 rounded-lg text-base cursor-pointer transition-colors hover:bg-primary-dark focus:outline-none focus:ring-2 focus:ring-primary/50 focus:ring-offset-2"
            >
                Try Again
            </button>
        </div>
    )
}

export default AuthError
