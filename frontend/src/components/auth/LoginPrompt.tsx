import { Button } from '@/components/shared/ui'
const LoginPrompt = ({ onLogin }: { onLogin: () => void }) => {
    return (
        <div className="flex justify-center items-center min-h-screen flex-col text-center p-8 bg-gradient-to-br from-primary to-primary-dark text-white">
            <h1 className="text-4xl font-bold mb-4">Memory App</h1>
            <p className="text-lg mb-8 opacity-90 max-w-md">Please log in to access your memory database</p>
            <Button
                onClick={onLogin}
                className="text-lg hover:-translate-y-0.5"
            >
                Log In
            </Button>
        </div>
    )
}

export default LoginPrompt
