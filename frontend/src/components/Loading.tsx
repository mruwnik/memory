const Loading = ({ message = "Loading..." }: { message?: string }) => {
    return (
        <div className="flex items-center min-h-screen flex-col justify-center">
            <h2 className="text-primary font-medium mb-4">{message}</h2>
            <div className="border-4 border-gray-200 border-t-primary rounded-full w-6 h-6 animate-spin"></div>
        </div>
    )
}

export default Loading
