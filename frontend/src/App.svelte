<script lang="ts">
  import { apiHelloHello } from "./generated/api";

  // Le SDK généré appelle /api en relatif : Vite proxifie en dev, nginx en
  // production. Aucune URL d'API dans le bundle, donc rien à reconfigurer.
  const greeting = apiHelloHello().then(({ data, error }) => {
    if (error || !data) {
      throw new Error("Requête /api/hello en échec");
    }
    return data.message;
  });
</script>

<main class="app">
  <h1>Dashboard Discord</h1>
  <p>Litestar + Svelte</p>
  <div class="card">
    {#await greeting}
      <p>Chargement…</p>
    {:then message}
      <p data-testid="greeting">{message}</p>
    {:catch error}
      <p data-testid="greeting-error">{error.message}</p>
    {/await}
  </div>
</main>

<style>
  .app {
    max-width: 1280px;
    margin: 0 auto;
    padding: 2rem;
    text-align: center;
  }

  .card {
    padding: 2em;
  }
</style>
