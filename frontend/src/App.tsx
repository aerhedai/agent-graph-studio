import { Canvas } from "./canvas/Canvas";
import "./App.css";

function App() {
  return (
    <div className="app">
      <header className="app__header">
        <h1>Agent Graph Studio</h1>
      </header>
      <Canvas />
    </div>
  );
}

export default App;
