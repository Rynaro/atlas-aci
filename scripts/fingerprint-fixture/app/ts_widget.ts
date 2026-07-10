class TsBase {
}

class TsHelper {
}

class TsWidget extends TsBase {
  constructor() {
    super();
    new TsHelper();
    tsExternalOnly();
  }
}
