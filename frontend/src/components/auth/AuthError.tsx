import React from 'react'

const AuthError = ({ error, onRetry }) => {
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