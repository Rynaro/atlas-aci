module RubyGreetable
  def greet
  end
end

class RubyBase
  def initialize
  end
end

class RubyWidget < RubyBase
  include RubyGreetable

  def initialize
    super
  end

  def build
    RubyWidget.new
  end
end
