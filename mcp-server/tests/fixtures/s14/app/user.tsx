import Default from "./default";
import { Header as AppHeader, Panel } from "./components";

export default function App() {
  return (
    <AppHeader>
      <Panel />
      <Default />
      <Missing />
      <div className="x" />
      <my-el />
      <svg:rect />
    </AppHeader>
  );
}
