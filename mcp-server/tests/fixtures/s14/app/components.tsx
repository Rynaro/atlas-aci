import React, { useState as useLocalState, type FC, Plain } from "react";
import * as Icons from "./icons";
import type { Props } from "./types";
import type TypeDefault from "./type-default";
import "./side-effect";

// NOTE: components stay small and structural
export function Header(props: Props) {
  const [open] = useLocalState(false);
  return (
    <header>
      <Icons.Logo />
      <Badge />
    </header>
  );
}

export class Panel extends React.Component {
  render() {
    return <Header />;
  }
  helper() {
    return makeWidget();
  }
}

export class Special extends Panel {}

export const Badge = () => {
  return (
    <span>
      {renderLabel()}
      <Header />
      <></>
    </span>
  );
};

const Inner = function () {
  return <div className="inner" />;
};

function renderLabel() {
  return "label";
}

function makeWidget() {
  return new Panel();
}

export { Inner };
export { Plain as Renamed } from "./plain";
