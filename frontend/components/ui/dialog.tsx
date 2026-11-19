"use client";

import { cva, type VariantProps } from "class-variance-authority";
import { TriangleAlert, XIcon } from "lucide-react";
import { Dialog as DialogPrimitive } from "radix-ui";
import * as React from "react";
import { cn } from "cn";

// Radix Dialog in the prototype's modal look, as one compound component:
//   <Dialog.Root> <Dialog.Content variant size> <Dialog.Title/> <Dialog.Description/> <Dialog.Footer/> ...
// Radix gives the focus trap, ESC / outside click to close and the aria wiring. The variant set on Content
// reaches the parts below it (a destructive dialog shows the warning mark above its title).

const contentVariants = cva(
  "fixed top-1/2 left-1/2 z-50 w-[calc(100%-3rem)] -translate-x-1/2 -translate-y-1/2 rounded-2xl bg-white p-6 shadow-2xl data-[state=open]:animate-in data-[state=open]:fade-in-0 data-[state=open]:zoom-in-95",
  {
    variants: { size: { sm: "max-w-sm", md: "max-w-md", lg: "max-w-lg" } },
    defaultVariants: { size: "md" },
  },
);

type Variant = "default" | "destructive";
const VariantContext = React.createContext<Variant>("default");

function Content({
  className,
  children,
  size,
  variant = "default",
  ...props
}: React.ComponentProps<typeof DialogPrimitive.Content> & VariantProps<typeof contentVariants> & { variant?: Variant }) {
  return (
    <DialogPrimitive.Portal>
      <DialogPrimitive.Overlay className="fixed inset-0 z-50 bg-gray-900/40 backdrop-blur-[2px] data-[state=open]:animate-in data-[state=open]:fade-in-0" />
      <DialogPrimitive.Content className={cn(contentVariants({ size }), className)} data-variant={variant} {...props}>
        <VariantContext.Provider value={variant}>{children}</VariantContext.Provider>
        <DialogPrimitive.Close className="absolute top-4 right-4 cursor-pointer rounded-md p-1 text-mute hover:bg-gray-100" aria-label="닫기">
          <XIcon className="size-4.5" />
        </DialogPrimitive.Close>
      </DialogPrimitive.Content>
    </DialogPrimitive.Portal>
  );
}

function Title({ className, children, ...props }: React.ComponentProps<typeof DialogPrimitive.Title>) {
  const variant = React.useContext(VariantContext);
  return (
    <>
      {variant === "destructive" && (
        <span aria-hidden className="mb-4 flex size-11 items-center justify-center rounded-full bg-red-50 text-red-500">
          <TriangleAlert size={20} />
        </span>
      )}
      <DialogPrimitive.Title className={cn("pr-8 text-lg font-semibold", className)} {...props}>{children}</DialogPrimitive.Title>
    </>
  );
}

function Description({ className, ...props }: React.ComponentProps<typeof DialogPrimitive.Description>) {
  return <DialogPrimitive.Description className={cn("mt-1.5 text-sm text-sub", className)} {...props} />;
}

function Footer({ className, ...props }: React.ComponentProps<"div">) {
  return <div className={cn("mt-6 flex justify-end gap-2", className)} {...props} />;
}

export const Dialog = {
  Root: DialogPrimitive.Root,
  Trigger: DialogPrimitive.Trigger,
  Close: DialogPrimitive.Close,
  Content,
  Title,
  Description,
  Footer,
};
