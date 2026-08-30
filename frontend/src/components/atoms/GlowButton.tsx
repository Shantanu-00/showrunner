"use client";

import React, { type ButtonHTMLAttributes, type ReactNode } from "react";
import { useHaptics } from "@/lib/useHaptics";
import { Loader2 } from "lucide-react";

export interface GlowButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: "primary" | "secondary" | "danger" | "ghost";
  size?: "sm" | "md" | "lg";
  icon?: React.ElementType;
  iconRight?: React.ElementType;
  loading?: boolean;
  fullWidth?: boolean;
  children?: ReactNode;
}

export function GlowButton({
  variant = "primary",
  size = "md",
  icon: Icon,
  iconRight: IconRight,
  loading = false,
  fullWidth = false,
  children,
  className = "",
  disabled,
  onClick,
  ...props
}: GlowButtonProps) {
  const { tapHaptic } = useHaptics();

  const handleClick = (e: React.MouseEvent<HTMLButtonElement>) => {
    if (disabled || loading) return;
    tapHaptic();
    onClick?.(e);
  };

  const sizeClasses = {
    sm: "py-2 px-4 text-xs min-h-[36px] gap-1.5",
    md: "py-3 px-6 text-sm min-h-[44px] gap-2",
    lg: "py-3.5 px-8 text-base min-h-[52px] gap-2.5",
  }[size];

  const variantClasses = {
    primary:
      "bg-gradient-to-r from-[var(--accent)] to-[var(--accent-soft)] text-slate-950 font-semibold shadow-[0_0_20px_-3px_var(--accent-glow)] hover:shadow-[0_0_28px_0px_var(--accent-glow)] hover:brightness-105 active:scale-[0.97]",
    secondary:
      "bg-[var(--bg-1)] border border-white/10 hover:border-white/25 text-[var(--text-primary)] hover:bg-[var(--bg-2)] active:scale-[0.97]",
    danger:
      "bg-gradient-to-r from-red-600 to-rose-500 text-white font-semibold shadow-[0_0_20px_-3px_rgba(239,68,68,0.4)] hover:shadow-[0_0_28px_0px_rgba(239,68,68,0.5)] active:scale-[0.97]",
    ghost:
      "bg-transparent hover:bg-white/5 text-[var(--text-primary)] active:scale-[0.97]",
  }[variant];

  return (
    <button
      type="button"
      disabled={disabled || loading}
      onClick={handleClick}
      className={`relative inline-flex items-center justify-center font-medium rounded-full transition-all duration-200 select-none cursor-pointer disabled:opacity-40 disabled:cursor-not-allowed disabled:active:scale-100 ${sizeClasses} ${variantClasses} ${
        fullWidth ? "w-full" : ""
      } ${className}`}
      {...props}
    >
      {loading ? (
        <Loader2 className="w-4 h-4 animate-spin shrink-0" />
      ) : (
        Icon && <Icon className="w-4 h-4 stroke-[2.2] shrink-0" />
      )}
      {children && <span>{children}</span>}
      {!loading && IconRight && <IconRight className="w-4 h-4 stroke-[2.2] shrink-0" />}
    </button>
  );
}
