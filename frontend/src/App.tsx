import { Canvas } from "./canvas/Canvas";
import { ThemeProvider } from "./theme/ThemeProvider";
import { ThemeToggle } from "./theme/ThemeToggle";
import "./App.css";

function App() {
  return (
    <ThemeProvider>
      <div className="app">
        <header className="app__header">
          <h1>Agent Graph Studio</h1>
          <ThemeToggle />
        </header>
        <Canvas />
      </div>
    </ThemeProvider>
  );
}

export default App;
