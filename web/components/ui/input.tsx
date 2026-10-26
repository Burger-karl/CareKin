import * as React from "react";
import { cn } from "@/lib/utils";

const field =
  "w-full rounded-lg border-[1.5px] border-line bg-white px-4 text-[15px] text-foreground placeholder:text-muted-foreground/80 focus-visible:border-primary disabled:cursor-not-allowed disabled:opacity-60 aria-[invalid=true]:border-crisis";

const Input = React.forwardRef<HTMLInputElement, React.InputHTMLAttributes<HTMLInputElement>>(
  ({ className, type = "text", ...props }, ref) => (
    <input ref={ref} type={type} className={cn(field, "h-12", className)} {...props} />
  ),
);
Input.displayName = "Input";

const Textarea = React.forwardRef<HTMLTextAreaElement, React.TextareaHTMLAttributes<HTMLTextAreaElement>>(
  ({ className, ...props }, ref) => (
    <textarea ref={ref} className={cn(field, "min-h-24 py-3", className)} {...props} />
  ),
);
Textarea.displayName = "Textarea";

const Select = React.forwardRef<HTMLSelectElement, React.SelectHTMLAttributes<HTMLSelectElement>>(
  ({ className, children, ...props }, ref) => (
    <select ref={ref} className={cn(field, "h-12 appearance-none bg-[url('/chevron.svg')] bg-[length:16px] bg-[right_1rem_center] bg-no-repeat pr-10", className)} {...props}>
      {children}
    </select>
  ),
);
Select.displayName = "Select";

export { Input, Textarea, Select };
