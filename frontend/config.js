window.TICKET_CONSOLE_CONFIG = {
  API_BASE_URL: "https://ticket-backend-api-48533944424.us-central1.run.app",
  USE_MOCK_API: false,
  FIREBASE_CONFIG: {
    apiKey: "AIzaSyCbzs7DJ7nqD8FELVgOKylABrohPyJg8Zs",
    // Use the Firebase Hosting origin as authDomain so that
    // signInWithRedirect lands on /__/auth/handler on the SAME origin
    // as the app. This avoids cross-site storage partitioning that
    // prevents getRedirectResult from reading the credential.
    // Use .firebaseapp.com (not .web.app) because the default OAuth client's
    // redirect URI is registered for firebaseapp.com. Both hostnames serve
    // the same Firebase Hosting site, so app users hit .firebaseapp.com too.
    authDomain: "msds-603-victors-demons.firebaseapp.com",
    projectId: "msds-603-victors-demons",
    appId: "1:48533944424:web:1caab7a98902277a3823dd",
    messagingSenderId: "48533944424",
    storageBucket: "msds-603-victors-demons.firebasestorage.app",
  },
};
