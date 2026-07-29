import * as React from "react"
import { Dialog as BaseDialog } from "@base-ui/react"
import { cn } from "@/lib/utils"

function DialogRoot(props: React.ComponentProps<typeof BaseDialog.Root>) {
  return <BaseDialog.Root {...props} />
}

function DialogTrigger(props: React.ComponentProps<typeof BaseDialog.Trigger>) {
  return <BaseDialog.Trigger {...props} />
}

function DialogPortal(props: React.ComponentProps<typeof BaseDialog.Portal>) {
  return <BaseDialog.Portal {...props} />
}

interface DialogOverlayProps extends React.ComponentProps<typeof BaseDialog.Backdrop> {
  className?: string
}

function DialogOverlay({ className, ...props }: DialogOverlayProps) {
  return (
    <BaseDialog.Backdrop
      className={cn(
        "fixed inset-0 z-50 bg-black/40 backdrop-blur-sm",
        className,
      )}
      {...props}
    />
  )
}

interface DialogContentProps extends React.ComponentProps<typeof BaseDialog.Popup> {
  className?: string
}

function DialogContent({ className, children, ...props }: DialogContentProps) {
  return (
    <BaseDialog.Portal>
      <DialogOverlay />
      <BaseDialog.Popup
        className={cn(
          "fixed left-1/2 top-1/2 z-50 -translate-x-1/2 -translate-y-1/2",
          "w-full max-w-md rounded-lg border bg-background shadow-lg",
          "p-6 focus:outline-none",
          className,
        )}
        {...props}
      >
        {children}
      </BaseDialog.Popup>
    </BaseDialog.Portal>
  )
}

interface DialogHeaderProps extends React.HTMLAttributes<HTMLDivElement> {}

function DialogHeader({ className, ...props }: DialogHeaderProps) {
  return (
    <div
      className={cn("flex flex-col gap-1.5 mb-4", className)}
      {...props}
    />
  )
}

interface DialogTitleProps extends React.ComponentProps<typeof BaseDialog.Title> {
  className?: string
}

function DialogTitle({ className, ...props }: DialogTitleProps) {
  return (
    <BaseDialog.Title
      className={cn("text-base font-semibold leading-none tracking-tight", className)}
      {...props}
    />
  )
}

interface DialogDescriptionProps extends React.ComponentProps<typeof BaseDialog.Description> {
  className?: string
}

function DialogDescription({ className, ...props }: DialogDescriptionProps) {
  return (
    <BaseDialog.Description
      className={cn("text-sm text-muted-foreground", className)}
      {...props}
    />
  )
}

interface DialogFooterProps extends React.HTMLAttributes<HTMLDivElement> {}

function DialogFooter({ className, ...props }: DialogFooterProps) {
  return (
    <div
      className={cn("flex justify-end gap-2 mt-6", className)}
      {...props}
    />
  )
}

function DialogClose(props: React.ComponentProps<typeof BaseDialog.Close>) {
  return <BaseDialog.Close {...props} />
}

export {
  DialogRoot,
  DialogTrigger,
  DialogPortal,
  DialogOverlay,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
  DialogClose,
}
