import { Navigate, Route, Routes } from "react-router-dom";
import { Layout } from "./components/Layout";
import { AutoReplyPage } from "./pages/AutoReply";
import { FilesPage } from "./pages/Files";
import { InboxPage } from "./pages/Inbox";
import { Login } from "./pages/Login";
import { SettingsPage } from "./pages/Settings";
import { TasksPage } from "./pages/Tasks";
import { WeComTodoPage } from "./pages/WeComTodo";

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route element={<Layout />}>
        <Route index element={<InboxPage />} />
        <Route path="/inbox" element={<InboxPage />} />
        <Route path="/tasks" element={<TasksPage />} />
        <Route path="/files" element={<FilesPage />} />
        <Route path="/auto-reply" element={<AutoReplyPage />} />
        <Route path="/settings" element={<SettingsPage />} />
        <Route path="/wecom/create-todo" element={<WeComTodoPage />} />
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
