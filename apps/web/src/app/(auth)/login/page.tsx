import { AuthForm } from "@/components/layout/AuthForm";

export default async function LoginPage({ searchParams }: { searchParams: Promise<{ error?: string }> }) {
  const { error } = await searchParams;
  return <AuthForm mode="login" initialError={error} />;
}
