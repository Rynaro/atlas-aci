module Outer
  class Inner::Thing
    def go
      ReportJob.perform_later(1, 2)
    end
  end
end

class ::Rooted
  def go
    ReportJob.perform_later(1, 2)
  end
end
