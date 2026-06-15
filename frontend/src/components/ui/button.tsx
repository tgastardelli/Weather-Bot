import type { ButtonHTMLAttributes } from "react"

import { cn } from "@/lib/utils"

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: "primary" | "ghost" | "outline"
  size?: "sm" | "md" | "icon"
}

export function Button({
  className,
  variant = "primary",
  size = "md",
  ...props
}: ButtonProps) {
  return (
    <button
      className={cn(
        "inline-flex items-center justify-center gap-2 rounded-md border font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-50",
        variant === "primary" &&
          "border-emerald-700 bg-emerald-700 text-white hover:bg-emerald-800",
        variant === "ghost" &&
          "border-transparent bg-transparent text-stone-700 hover:bg-stone-100",
        variant === "outline" &&
          "border-stone-300 bg-white text-stone-800 hover:bg-stone-50",
        size === "sm" && "h-8 px-3 text-sm",
        size === "md" && "h-10 px-4 text-sm",
        size === "icon" && "h-9 w-9 p-0",
        className,
      )}
      {...props}
    />
  )
}
