import { saudação, Café, type Pessoa } from "./greeting";
const visitor: Pessoa = { nome: "Ana" };
const cup = new Café();
console.log(saudação(visitor), cup.brew());
