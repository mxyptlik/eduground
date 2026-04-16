import React from "react";
import ReactDOM from "react-dom/client";
import { ClerkProvider } from "@clerk/clerk-react";
import { BrowserRouter } from "react-router-dom";
import { App } from "@/App";
import { AppSessionProvider } from "@/lib/session/AppSessionContext";
import "@/styles/global.css";

const clerkPublishableKey = import.meta.env.VITE_CLERK_PUBLISHABLE_KEY;
const hasUsableClerkKey =
  typeof clerkPublishableKey === "string" &&
  clerkPublishableKey.trim().length > 0 &&
  !/(replace_me|placeholder|dummy|your-)/i.test(clerkPublishableKey);

function MissingClerkConfiguration() {
  return (
    <React.StrictMode>
      <div className="auth-page">
        <div className="auth-panel">
          <p className="eyebrow">Configuration required</p>
          <h2>Clerk is not configured.</h2>
          <p className="page-description">
            Set <code>VITE_CLERK_PUBLISHABLE_KEY</code> in your frontend environment before
            starting the web app.
          </p>
        </div>
      </div>
    </React.StrictMode>
  );
}

ReactDOM.createRoot(document.getElementById("root") as HTMLElement).render(
  hasUsableClerkKey ? (
    <React.StrictMode>
      <ClerkProvider publishableKey={clerkPublishableKey}>
        <AppSessionProvider>
          <BrowserRouter>
            <App />
          </BrowserRouter>
        </AppSessionProvider>
      </ClerkProvider>
    </React.StrictMode>
  ) : (
    <MissingClerkConfiguration />
  ),
);
