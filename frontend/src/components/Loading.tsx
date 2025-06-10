const Loading = ({ message = "Loading..." }: { message?: string }) => {
    return (
        <div className="loading">
            <h2>{message}</h2>
            <div className="loading-spinner"></div>
        </div>
    )
}

export default Loading 